package main

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"slices"
	"time"

	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/types"
)

const (
	profileInterval = 24 * time.Hour
	// One usync query answers many accounts at once; pictures are a request each, paced so a
	// first refresh of every contact does not read as a burst.
	userInfoBatch = 100
	picturePause  = 500 * time.Millisecond
)

// refreshProfiles keeps each account's about text and picture current, daily, for as long as
// the bridge runs. A picture is fetched only when its id changed or its file is gone.
func (b *bridge) refreshProfiles() {
	tick := time.Tick(profileInterval)
	for {
		if err := b.refreshProfilesOnce(); err != nil {
			log.Printf("profile refresh failed: %v", err)
		}
		select {
		case <-b.ctx.Done():
			return
		case <-tick:
		}
	}
}

func (b *bridge) refreshProfilesOnce() error {
	stored, err := b.store.ProfileJIDs()
	if err != nil {
		return err
	}
	archived, err := archiveAccounts(b.archive)
	if err != nil {
		return fmt.Errorf("reading the archive's accounts: %w", err)
	}
	accounts := slices.Concat(stored, archived)
	slices.Sort(accounts)
	accounts = slices.Compact(accounts)
	jids := make([]types.JID, 0, len(accounts))
	for _, raw := range accounts {
		jid, err := types.ParseJID(raw)
		if err != nil || jid.Server != types.DefaultUserServer {
			continue
		}
		jids = append(jids, jid)
	}

	pictures := 0
	for start := 0; start < len(jids); start += userInfoBatch {
		batch := jids[start:min(start+userInfoBatch, len(jids))]
		infos, err := b.cli.GetUserInfo(b.ctx, batch)
		if err != nil {
			return fmt.Errorf("user info for %d account(s): %w", len(batch), err)
		}
		for jid, info := range infos {
			path, err := b.picture(jid, info.PictureID)
			if err != nil {
				log.Printf("picture of %s: %v", jid, err)
				continue
			}
			if path != "" {
				pictures++
			}
			if err := b.store.UpsertProfile(jid.String(), info.Status, info.PictureID, path); err != nil {
				return err
			}
		}
	}
	log.Printf("refreshed %d profile(s), fetched %d picture(s)", len(jids), pictures)
	return nil
}

// archiveAccounts is every account the archive holds, most of which no message in this mirror
// names: senders ingested from phone backups that predate the bridge.
func archiveAccounts(path string) ([]string, error) {
	if path == "" {
		return nil, nil
	}
	if _, err := os.Stat(path); errors.Is(err, os.ErrNotExist) {
		return nil, nil
	}
	db, err := sql.Open("sqlite3", "file:"+path+"?mode=ro")
	if err != nil {
		return nil, err
	}
	defer db.Close()
	rows, err := db.Query("SELECT profile_id FROM profiles")
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var jids []string
	for rows.Next() {
		var jid string
		if err := rows.Scan(&jid); err != nil {
			return nil, err
		}
		jids = append(jids, jid)
	}
	return jids, rows.Err()
}

// picture returns the file just fetched, or "" when there was nothing new to fetch.
func (b *bridge) picture(jid types.JID, id string) (string, error) {
	if known, held := b.store.PictureID(jid.String()); id == "" || (known == id && held) {
		return "", nil
	}
	time.Sleep(picturePause)
	info, err := b.cli.GetProfilePictureInfo(b.ctx, jid, &whatsmeow.GetProfilePictureParams{})
	if errors.Is(err, whatsmeow.ErrProfilePictureNotSet) || errors.Is(err, whatsmeow.ErrProfilePictureUnauthorized) {
		return "", nil
	}
	if err != nil {
		return "", err
	}
	path := filepath.Join(b.store.Dir, "profiles", jid.User, info.ID+".jpg")
	return path, download(b.ctx, info.URL, path)
}

func download(ctx context.Context, url, path string) error {
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return err
	}
	response, err := http.DefaultClient.Do(request)
	if err != nil {
		return err
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return fmt.Errorf("HTTP %d", response.StatusCode)
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	file, err := os.Create(path)
	if err != nil {
		return err
	}
	if _, err := io.Copy(file, response.Body); err != nil {
		file.Close()
		return err
	}
	return file.Close()
}
