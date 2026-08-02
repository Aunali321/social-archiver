package main

import (
	"time"

	"go.mau.fi/whatsmeow/proto/waCommon"
	"go.mau.fi/whatsmeow/proto/waE2E"
	"go.mau.fi/whatsmeow/types"
	"go.mau.fi/whatsmeow/types/events"
)

// ParsedMedia is the decryption envelope, not the file: WhatsApp media downloads need the
// direct path plus the key and both hashes, and expires off the CDN, which is why every
// field is persisted even after a successful download.
type ParsedMedia struct {
	Type          string // image, video, gif, audio, document, sticker
	Caption       string
	MimeType      string
	DirectPath    string
	MediaKey      []byte
	FileSHA256    []byte
	FileEncSHA256 []byte
	FileLength    uint64
}

type Parsed struct {
	Chat      types.JID
	ID        string
	Sender    types.JID
	PushName  string
	FromMe    bool
	Ts        time.Time
	Text      string
	QuotedID  string
	Media     *ParsedMedia
	ViewOnce  bool
	Ephemeral bool
	Edited    bool
	Revoke    bool // marks the target (Chat, ID) revoked rather than inserting a row

	// A view-once media envelope recovered from this message's quoted context. The server
	// withholds view-once payloads from linked devices, but a reply's quote is built by the
	// replying phone inside the E2E payload, and the original envelope can ride along.
	QuotedViewOnce *ParsedMedia
}

// ParseEvent walks the proto for live and history events alike — history WebMessageInfos
// come through client.ParseWebMessage first, so the view-once/ephemeral wrappers whatsmeow
// unwraps on live events are unwrapped there too; the explicit unwrapping below is for the
// paths where the wrapper survives into evt.Message.
func ParseEvent(evt *events.Message) *Parsed {
	p := &Parsed{
		Chat:      evt.Info.Chat,
		ID:        evt.Info.ID,
		Sender:    evt.Info.Sender,
		PushName:  evt.Info.PushName,
		FromMe:    evt.Info.IsFromMe,
		Ts:        evt.Info.Timestamp,
		ViewOnce:  evt.IsViewOnce || evt.IsViewOnceV2,
		Ephemeral: evt.IsEphemeral,
	}
	extract(evt.Message, p)
	return p
}

func extract(m *waE2E.Message, p *Parsed) {
	if m == nil {
		return
	}
	if ds := m.GetDeviceSentMessage(); ds.GetMessage() != nil {
		p.FromMe = true
		if jid, err := types.ParseJID(ds.GetDestinationJID()); err == nil {
			p.Chat = jid
		}
		extract(ds.GetMessage(), p)
		return
	}
	if inner := m.GetEphemeralMessage().GetMessage(); inner != nil {
		p.Ephemeral = true
		extract(inner, p)
		return
	}
	for _, inner := range []*waE2E.Message{
		m.GetViewOnceMessage().GetMessage(),
		m.GetViewOnceMessageV2().GetMessage(),
		m.GetViewOnceMessageV2Extension().GetMessage(),
	} {
		if inner != nil {
			p.ViewOnce = true
			extract(inner, p)
			return
		}
	}
	if inner := m.GetDocumentWithCaptionMessage().GetMessage(); inner != nil {
		extract(inner, p)
		return
	}
	if inner := m.GetEditedMessage().GetMessage(); inner != nil {
		p.Edited = true
		extract(inner, p)
		return
	}
	if protocol := m.GetProtocolMessage(); protocol != nil {
		switch protocol.GetType() {
		case waE2E.ProtocolMessage_REVOKE:
			applyKey(protocol.GetKey(), p)
			p.Revoke = true
		case waE2E.ProtocolMessage_MESSAGE_EDIT:
			applyKey(protocol.GetKey(), p)
			p.Edited = true
			extract(protocol.GetEditedMessage(), p)
		}
		return // every other protocol message is bookkeeping, not content
	}

	if text := m.GetConversation(); text != "" {
		p.Text = text
	}
	if ext := m.GetExtendedTextMessage(); ext != nil {
		p.Text = ext.GetText()
		applyContext(ext.GetContextInfo(), p)
	}
	extractMedia(m, p)
}

// The revoke/edit key names the target message; the row identity moves to it.
func applyKey(key *waCommon.MessageKey, p *Parsed) {
	if key == nil {
		return
	}
	if key.GetID() != "" {
		p.ID = key.GetID()
	}
	if remote := key.GetRemoteJID(); remote != "" {
		if jid, err := types.ParseJID(remote); err == nil {
			p.Chat = jid
		}
	}
	p.FromMe = key.GetFromMe()
}

func applyContext(ctx *waE2E.ContextInfo, p *Parsed) {
	if ctx.GetStanzaID() != "" {
		p.QuotedID = ctx.GetStanzaID()
	}
	if quoted := ctx.GetQuotedMessage(); quoted != nil {
		inner := &Parsed{}
		extract(quoted, inner)
		if inner.ViewOnce && inner.Media != nil && inner.Media.DirectPath != "" {
			p.QuotedViewOnce = inner.Media
		}
	}
}

func extractMedia(m *waE2E.Message, p *Parsed) {
	if img := m.GetImageMessage(); img != nil {
		p.setMedia("image", img.GetCaption(), img.GetMimetype(), img.GetDirectPath(),
			img.GetMediaKey(), img.GetFileSHA256(), img.GetFileEncSHA256(), img.GetFileLength())
		p.ViewOnce = p.ViewOnce || img.GetViewOnce()
		applyContext(img.GetContextInfo(), p)
	}
	if vid := m.GetVideoMessage(); vid != nil {
		kind := "video"
		if vid.GetGifPlayback() {
			kind = "gif"
		}
		p.setMedia(kind, vid.GetCaption(), vid.GetMimetype(), vid.GetDirectPath(),
			vid.GetMediaKey(), vid.GetFileSHA256(), vid.GetFileEncSHA256(), vid.GetFileLength())
		p.ViewOnce = p.ViewOnce || vid.GetViewOnce()
		applyContext(vid.GetContextInfo(), p)
	}
	if aud := m.GetAudioMessage(); aud != nil {
		p.setMedia("audio", "", aud.GetMimetype(), aud.GetDirectPath(),
			aud.GetMediaKey(), aud.GetFileSHA256(), aud.GetFileEncSHA256(), aud.GetFileLength())
		p.ViewOnce = p.ViewOnce || aud.GetViewOnce()
		applyContext(aud.GetContextInfo(), p)
	}
	if doc := m.GetDocumentMessage(); doc != nil {
		p.setMedia("document", doc.GetCaption(), doc.GetMimetype(), doc.GetDirectPath(),
			doc.GetMediaKey(), doc.GetFileSHA256(), doc.GetFileEncSHA256(), doc.GetFileLength())
		applyContext(doc.GetContextInfo(), p)
	}
	if sticker := m.GetStickerMessage(); sticker != nil {
		p.setMedia("sticker", "", sticker.GetMimetype(), sticker.GetDirectPath(),
			sticker.GetMediaKey(), sticker.GetFileSHA256(), sticker.GetFileEncSHA256(), sticker.GetFileLength())
		applyContext(sticker.GetContextInfo(), p)
	}
}

func (p *Parsed) setMedia(kind, caption, mime, directPath string, key, sha, encSHA []byte, length uint64) {
	if p.Text == "" {
		p.Text = caption
	}
	p.Media = &ParsedMedia{
		Type: kind, Caption: caption, MimeType: mime, DirectPath: directPath,
		MediaKey: cloneBytes(key), FileSHA256: cloneBytes(sha), FileEncSHA256: cloneBytes(encSHA),
		FileLength: length,
	}
}

// The proto owns its byte slices; copies keep the stored envelope alive past the event.
func cloneBytes(b []byte) []byte {
	if len(b) == 0 {
		return nil
	}
	out := make([]byte, len(b))
	copy(out, b)
	return out
}
