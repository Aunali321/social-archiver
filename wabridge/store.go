package main

import (
	"database/sql"
	"path/filepath"
	"time"

	_ "github.com/mattn/go-sqlite3"
)

// The mirror the Python archiver ingests. One row per message, keyed the same way the
// archive keys items: (chat_jid, msg_id). Media crypto fields are kept so an interrupted
// download can resume; local_path is what the archiver links from.
const schema = `
CREATE TABLE IF NOT EXISTS chats (
	jid TEXT PRIMARY KEY,
	kind TEXT NOT NULL,
	name TEXT
);
CREATE TABLE IF NOT EXISTS contacts (
	jid TEXT PRIMARY KEY,
	name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
	chat_jid TEXT NOT NULL,
	msg_id TEXT NOT NULL,
	sender_jid TEXT,
	sender_name TEXT,
	from_me INTEGER NOT NULL,
	ts INTEGER NOT NULL,
	text TEXT,
	quoted_msg_id TEXT,
	media_type TEXT,
	mime_type TEXT,
	direct_path TEXT,
	media_key BLOB,
	file_sha256 BLOB,
	file_enc_sha256 BLOB,
	file_length INTEGER,
	local_path TEXT,
	media_error TEXT,
	view_once INTEGER NOT NULL DEFAULT 0,
	ephemeral INTEGER NOT NULL DEFAULT 0,
	revoked INTEGER NOT NULL DEFAULT 0,
	edited INTEGER NOT NULL DEFAULT 0,
	PRIMARY KEY (chat_jid, msg_id)
);
CREATE INDEX IF NOT EXISTS idx_messages_ts ON messages(ts);
CREATE INDEX IF NOT EXISTS idx_messages_pending_media ON messages(chat_jid, msg_id)
	WHERE direct_path IS NOT NULL AND local_path IS NULL AND media_error IS NULL;
`

type Store struct {
	db  *sql.DB
	Dir string
}

type StoredMessage struct {
	ChatJID    string
	MsgID      string
	SenderJID  string
	SenderName string
	FromMe     bool
	Ts         time.Time
	Text       string
	QuotedID   string
	Media      *ParsedMedia
	ViewOnce   bool
	Ephemeral  bool
	Edited     bool
}

type MediaJob struct {
	ChatJID    string
	MsgID      string
	MediaType  string
	MimeType   string
	DirectPath string
	MediaKey   []byte
	FileSHA    []byte
	FileEncSHA []byte
	FileLength uint64
}

func OpenStore(dir string) (*Store, error) {
	db, err := sql.Open("sqlite3", "file:"+filepath.Join(dir, "wabridge.db")+"?_foreign_keys=on&_busy_timeout=5000")
	if err != nil {
		return nil, err
	}
	for _, pragma := range []string{"PRAGMA journal_mode=WAL", "PRAGMA synchronous=NORMAL"} {
		if _, err := db.Exec(pragma); err != nil {
			return nil, err
		}
	}
	if _, err := db.Exec(schema); err != nil {
		return nil, err
	}
	return &Store{db: db, Dir: dir}, nil
}

func (s *Store) Close() error { return s.db.Close() }

func (s *Store) UpsertChat(jid, kind, name string) error {
	_, err := s.db.Exec(`
		INSERT INTO chats (jid, kind, name) VALUES (?, ?, ?)
		ON CONFLICT(jid) DO UPDATE SET name = CASE WHEN excluded.name != '' THEN excluded.name ELSE name END`,
		jid, kind, name)
	return err
}

func (s *Store) UpsertContact(jid, name string) error {
	if name == "" {
		return nil
	}
	_, err := s.db.Exec(`
		INSERT INTO contacts (jid, name) VALUES (?, ?)
		ON CONFLICT(jid) DO UPDATE SET name = excluded.name`, jid, name)
	return err
}

func (s *Store) ContactName(jid string) string {
	var name string
	_ = s.db.QueryRow("SELECT name FROM contacts WHERE jid = ?", jid).Scan(&name)
	return name
}

// Insert reports whether the row is new. Re-deliveries (history sync repeating a live
// message) are dropped rather than merged: both carry the same content.
func (s *Store) Insert(m *StoredMessage) (bool, error) {
	var mediaType, mimeType, directPath any
	var mediaKey, fileSHA, fileEncSHA []byte
	var fileLength any
	if m.Media != nil {
		mediaType, mimeType = m.Media.Type, m.Media.MimeType
		// NULL, not "": history sync delivers some media without a path, and the pending-media
		// query must not keep offering those for download
		if m.Media.DirectPath != "" {
			directPath = m.Media.DirectPath
		}
		mediaKey, fileSHA, fileEncSHA = m.Media.MediaKey, m.Media.FileSHA256, m.Media.FileEncSHA256
		fileLength = m.Media.FileLength
	}
	result, err := s.db.Exec(`
		INSERT INTO messages (chat_jid, msg_id, sender_jid, sender_name, from_me, ts, text, quoted_msg_id,
			media_type, mime_type, direct_path, media_key, file_sha256, file_enc_sha256, file_length,
			view_once, ephemeral, edited)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
		ON CONFLICT(chat_jid, msg_id) DO NOTHING`,
		m.ChatJID, m.MsgID, m.SenderJID, m.SenderName, m.FromMe, m.Ts.Unix(), m.Text, m.QuotedID,
		mediaType, mimeType, directPath, mediaKey, fileSHA, fileEncSHA, fileLength,
		m.ViewOnce, m.Ephemeral, m.Edited)
	if err != nil {
		return false, err
	}
	rows, err := result.RowsAffected()
	return rows > 0, err
}

// MarkRevoked keeps the content: the archive exists to outlive deletions. The flag records
// that WhatsApp no longer shows the message.
func (s *Store) MarkRevoked(chatJID, msgID string) error {
	_, err := s.db.Exec("UPDATE messages SET revoked = 1 WHERE chat_jid = ? AND msg_id = ?", chatJID, msgID)
	return err
}

// ApplyEdit reports whether the target existed; an edit of an unseen message inserts instead.
func (s *Store) ApplyEdit(chatJID, msgID, text string) (bool, error) {
	result, err := s.db.Exec(
		"UPDATE messages SET text = ?, edited = 1 WHERE chat_jid = ? AND msg_id = ?", text, chatJID, msgID)
	if err != nil {
		return false, err
	}
	rows, err := result.RowsAffected()
	return rows > 0, err
}

func (s *Store) SetMediaResult(chatJID, msgID, localPath, mediaError string) error {
	_, err := s.db.Exec(`
		UPDATE messages SET local_path = NULLIF(?, ''), media_error = NULLIF(?, '')
		WHERE chat_jid = ? AND msg_id = ?`, localPath, mediaError, chatJID, msgID)
	return err
}

func (s *Store) PendingMedia() ([]MediaJob, error) {
	rows, err := s.db.Query(`
		SELECT chat_jid, msg_id, media_type, mime_type, direct_path, media_key, file_sha256, file_enc_sha256, file_length
		FROM messages WHERE direct_path IS NOT NULL AND local_path IS NULL AND media_error IS NULL`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var jobs []MediaJob
	for rows.Next() {
		var job MediaJob
		if err := rows.Scan(&job.ChatJID, &job.MsgID, &job.MediaType, &job.MimeType, &job.DirectPath,
			&job.MediaKey, &job.FileSHA, &job.FileEncSHA, &job.FileLength); err != nil {
			return nil, err
		}
		jobs = append(jobs, job)
	}
	return jobs, rows.Err()
}
