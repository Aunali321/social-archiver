package main

import (
	"context"
	"errors"
	"fmt"
	"log"
	"os"
	"path/filepath"
	"strings"

	"go.mau.fi/whatsmeow"
)

const mediaWorkers = 4

var mediaTypes = map[string]whatsmeow.MediaType{
	"image":    whatsmeow.MediaImage,
	"sticker":  whatsmeow.MediaImage,
	"video":    whatsmeow.MediaVideo,
	"gif":      whatsmeow.MediaVideo, // gifs are videos with a playback hint
	"audio":    whatsmeow.MediaAudio,
	"document": whatsmeow.MediaDocument,
}

var extensions = map[string]string{
	"image/jpeg":      ".jpg",
	"image/png":       ".png",
	"image/webp":      ".webp",
	"image/gif":       ".gif",
	"video/mp4":       ".mp4",
	"audio/ogg":       ".ogg",
	"audio/mp4":       ".m4a",
	"audio/mpeg":      ".mp3",
	"audio/aac":       ".aac",
	"application/pdf": ".pdf",
}

type mediaPool struct {
	cli   *whatsmeow.Client
	store *Store
	jobs  chan MediaJob
}

func newMediaPool(ctx context.Context, cli *whatsmeow.Client, store *Store) *mediaPool {
	pool := &mediaPool{cli: cli, store: store, jobs: make(chan MediaJob, 512)}
	for range mediaWorkers {
		go pool.worker(ctx)
	}
	return pool
}

// Enqueue never blocks the event handler: a full queue is deferred to the next backlog
// sweep rather than stalling the websocket.
func (p *mediaPool) Enqueue(job MediaJob) {
	select {
	case p.jobs <- job:
	default:
		log.Printf("media queue full, %s/%s waits for the next backlog sweep", job.ChatJID, job.MsgID)
	}
}

func (p *mediaPool) EnqueueBacklog() {
	jobs, err := p.store.PendingMedia()
	if err != nil {
		log.Printf("media backlog query failed: %v", err)
		return
	}
	if len(jobs) > 0 {
		log.Printf("media backlog: %d file(s) pending", len(jobs))
	}
	for _, job := range jobs {
		p.Enqueue(job)
	}
}

func (p *mediaPool) worker(ctx context.Context) {
	for {
		select {
		case <-ctx.Done():
			return
		case job := <-p.jobs:
			if err := p.download(ctx, job); err != nil {
				log.Printf("media %s/%s: %v", job.ChatJID, job.MsgID, err)
			}
		}
	}
}

func (p *mediaPool) download(ctx context.Context, job MediaJob) error {
	mediaType, ok := mediaTypes[job.MediaType]
	if !ok {
		return p.store.SetMediaResult(job.ChatJID, job.MsgID, "", "unsupported media type "+job.MediaType)
	}
	if job.DirectPath == "" {
		return p.store.SetMediaResult(job.ChatJID, job.MsgID, "", "no direct path; only a media retry could recover this")
	}
	target := filepath.Join(p.store.Dir, "media", sanitize(job.ChatJID), sanitize(job.MsgID)+extension(job.MimeType))
	if err := os.MkdirAll(filepath.Dir(target), 0o700); err != nil {
		return err
	}

	tmp, err := os.CreateTemp(filepath.Dir(target), ".wabridge-*")
	if err != nil {
		return err
	}
	defer os.Remove(tmp.Name())
	err = p.cli.DownloadMediaWithPathToFile(ctx, job.DirectPath, job.FileEncSHA, job.FileSHA, job.MediaKey, mediaType, "", false, tmp)
	closeErr := tmp.Close()
	if err == nil {
		err = closeErr
	}
	if err != nil {
		if expired(err) {
			// The CDN no longer has it. Recorded so the download is not retried forever;
			// the media-retry protocol (asking the phone to re-upload) is a later addition.
			return p.store.SetMediaResult(job.ChatJID, job.MsgID, "", "expired: "+err.Error())
		}
		return fmt.Errorf("download: %w", err)
	}
	if err := os.Rename(tmp.Name(), target); err != nil {
		return err
	}
	return p.store.SetMediaResult(job.ChatJID, job.MsgID, target, "")
}

func expired(err error) bool {
	return errors.Is(err, whatsmeow.ErrMediaDownloadFailedWith403) ||
		errors.Is(err, whatsmeow.ErrMediaDownloadFailedWith404) ||
		errors.Is(err, whatsmeow.ErrMediaDownloadFailedWith410)
}

func extension(mime string) string {
	base, _, _ := strings.Cut(mime, ";")
	if ext, ok := extensions[strings.TrimSpace(base)]; ok {
		return ext
	}
	return ".bin"
}

func sanitize(name string) string {
	return strings.Map(func(r rune) rune {
		switch {
		case r >= 'a' && r <= 'z', r >= 'A' && r <= 'Z', r >= '0' && r <= '9',
			r == '@', r == '.', r == '-', r == '_':
			return r
		default:
			return '_'
		}
	}, name)
}
