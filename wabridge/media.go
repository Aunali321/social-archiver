package main

import (
	"context"
	"errors"
	"fmt"
	"log"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"

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

// mediaPool downloads what the store lists as pending. The store is the queue: a sweep hands
// every pending file not already in flight to the workers, waiting for a free one rather
// than dropping it, so a burst far larger than the workers (the history push after
// pairing) is downloaded in full.
type mediaPool struct {
	cli   *whatsmeow.Client
	store *Store
	jobs  chan MediaJob
	wake  chan struct{}

	mu       sync.Mutex
	inflight map[string]bool
	// Downloads that failed without expiring wait here for the next tick, so a file that
	// keeps failing is retried every sweepInterval rather than on every incoming message
	failed map[string]bool
}

const sweepInterval = 10 * time.Minute

func newMediaPool(ctx context.Context, cli *whatsmeow.Client, store *Store) *mediaPool {
	pool := &mediaPool{
		cli: cli, store: store, jobs: make(chan MediaJob), wake: make(chan struct{}, 1),
		inflight: map[string]bool{}, failed: map[string]bool{},
	}
	for range mediaWorkers {
		go pool.worker(ctx)
	}
	return pool
}

// Wake asks for a sweep without ever blocking the event handler; wakes that arrive while
// one is pending collapse into it.
func (p *mediaPool) Wake() {
	select {
	case p.wake <- struct{}{}:
	default:
	}
}

// Feed sweeps once connected, then again on every wake and every sweepInterval.
func (p *mediaPool) Feed(ctx context.Context) {
	ticker := time.NewTicker(sweepInterval)
	defer ticker.Stop()
	scheduled := true
	for {
		if !p.sweep(ctx, scheduled) {
			return
		}
		select {
		case <-ctx.Done():
			return
		case <-p.wake:
			scheduled = false
		case <-ticker.C:
			p.mu.Lock()
			clear(p.failed)
			p.mu.Unlock()
			scheduled = true
		}
	}
}

// sweep returns false once the context ends. Scheduled sweeps report the backlog; a wake
// per incoming message would flood the log.
func (p *mediaPool) sweep(ctx context.Context, scheduled bool) bool {
	pending, err := p.store.PendingMedia()
	if err != nil {
		log.Printf("media backlog query failed: %v", err)
		return true
	}
	if scheduled && len(pending) > 0 {
		log.Printf("media backlog: %d file(s) pending", len(pending))
	}
	for _, job := range pending {
		if !p.claim(job.key()) {
			continue
		}
		select {
		case p.jobs <- job:
		case <-ctx.Done():
			return false
		}
	}
	return true
}

func (p *mediaPool) claim(key string) bool {
	p.mu.Lock()
	defer p.mu.Unlock()
	if p.inflight[key] || p.failed[key] {
		return false
	}
	p.inflight[key] = true
	return true
}

func (p *mediaPool) release(key string, failed bool) {
	p.mu.Lock()
	defer p.mu.Unlock()
	delete(p.inflight, key)
	if failed {
		p.failed[key] = true
	}
}

func (p *mediaPool) worker(ctx context.Context) {
	for {
		select {
		case <-ctx.Done():
			return
		case job := <-p.jobs:
			err := p.download(ctx, job)
			if err != nil {
				log.Printf("media %s: %v", job.key(), err)
			}
			p.release(job.key(), err != nil)
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
