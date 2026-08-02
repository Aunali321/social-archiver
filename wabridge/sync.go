package main

import (
	"context"
	"errors"
	"log"
	"runtime/debug"
	"strings"
	"sync"

	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/proto/waE2E"
	"go.mau.fi/whatsmeow/proto/waHistorySync"
	"go.mau.fi/whatsmeow/types"
	"go.mau.fi/whatsmeow/types/events"
)

type bridge struct {
	ctx       context.Context
	cli       *whatsmeow.Client
	store     *Store
	media     *mediaPool
	refreshed bool
	historyMu sync.Mutex
	loggedOut chan struct{}
	once      sync.Once
}

// runSync is the daemon: connect, mirror every message and history-sync payload into the
// store, download media eagerly, and keep running until the context ends. It never sends
// anything and never marks anything read.
//
// Pairing happens in here too, when allowed: the freshly paired connection stays up and
// simply becomes the sync. A separate pair-then-exit step loses twice — the phone rolls
// back a device that disconnects right after scanning, and the one-time history push that
// follows pairing lands before any second process could take over.
func runSync(ctx context.Context, cli *whatsmeow.Client, store *Store, pair bool, phone, qrFile string) error {
	b := &bridge{ctx: ctx, cli: cli, store: store, media: newMediaPool(ctx, cli, store), loggedOut: make(chan struct{})}

	// Manual mode delivers history blobs as notifications instead of downloading them
	// behind our back, so ingest controls the timing and sees every payload. Set before
	// connecting, so the pairing push is already ours.
	cli.ManualHistorySyncDownload = true
	cli.AddEventHandler(b.handle)

	var qrChan <-chan whatsmeow.QRChannelItem
	if cli.Store.ID == nil {
		if !pair {
			return errors.New("not paired; pair from the web UI or run `wabridge -pair sync`")
		}
		ch, err := cli.GetQRChannel(ctx)
		if err != nil {
			return err
		}
		qrChan = ch
	}
	if err := cli.Connect(); err != nil {
		return err
	}
	if qrChan != nil {
		if err := pairLoop(ctx, cli, qrChan, phone, qrFile); err != nil {
			return err
		}
	}

	log.Printf("syncing as %s into %s", cli.Store.ID, store.Dir)
	b.media.EnqueueBacklog()

	select {
	case <-ctx.Done():
		cli.Disconnect()
		return nil
	case <-b.loggedOut:
		// Exiting is what lets the supervisor see the dead session and offer pairing
		// again, instead of a zombie holding credentials the phone already revoked.
		cli.Disconnect()
		return errors.New("logged out by the phone; pair again")
	}
}

func (b *bridge) handle(evt any) {
	// Unexpected protos do occur; one bad event must not take the daemon down.
	defer func() {
		if r := recover(); r != nil {
			log.Printf("panic handling %T: %v\n%s", evt, r, debug.Stack())
		}
	}()

	switch v := evt.(type) {
	case *events.Message:
		if notif := v.Message.GetProtocolMessage().GetHistorySyncNotification(); notif != nil {
			// Off the event loop: a payload holds thousands of messages, and every live
			// message delivered after it would otherwise wait in line behind the ingest.
			// The mutex keeps payloads ordered among themselves.
			go func() {
				b.historyMu.Lock()
				defer b.historyMu.Unlock()
				b.historyNotification(notif)
			}()
			return
		}
		b.storeMessage(v)
	case *events.HistorySync:
		b.ingestHistory(v.Data)
	case *events.UndecryptableMessage:
		if v.UnavailableType == events.UnavailableTypeViewOnce {
			chat := b.canonical(v.Info.Chat)
			sender := b.canonical(v.Info.Sender)
			_ = b.store.RecordStub(chat.String(), v.Info.ID, sender.String(),
				b.store.ContactName(sender.String()), v.Info.Timestamp.Unix(), "view_once")
			log.Printf("view-once stub recorded from %s; reply to it on the phone to recover the media", sender)
		}
	case *events.Connected:
		log.Printf("connected")
		if !b.refreshed {
			b.refreshed = true
			go b.refreshNames()
		}
	case *events.LoggedOut:
		b.once.Do(func() { close(b.loggedOut) })
	case *events.StreamReplaced:
		log.Printf("another client took over this session; disconnecting")
		b.cli.Disconnect()
	}
}

func (b *bridge) historyNotification(notif *waE2E.HistorySyncNotification) {
	if notif.GetSyncType() == waE2E.HistorySyncType_ON_DEMAND {
		return // only requested backfills produce these, and the bridge requests none
	}
	data, err := b.cli.DownloadHistorySync(b.ctx, notif, true)
	if err != nil {
		log.Printf("history sync download failed: %v", err)
		return
	}
	b.ingestHistory(data)
}

func (b *bridge) ingestHistory(data *waHistorySync.HistorySync) {
	if data == nil {
		return
	}
	stored, failed := 0, 0
	for _, conv := range data.GetConversations() {
		chatJID, err := types.ParseJID(strings.TrimSpace(conv.GetID()))
		if err != nil {
			continue
		}
		for _, historyMsg := range conv.GetMessages() {
			web := historyMsg.GetMessage()
			if web == nil {
				continue
			}
			// ParseWebMessage unwraps view-once/ephemeral/device-sent the same way live
			// events are unwrapped; hand-parsing WebMessageInfo would miss wrapped media.
			evt, err := b.cli.ParseWebMessage(chatJID, web)
			if err != nil {
				failed++
				continue
			}
			if b.storeMessage(evt) {
				stored++
			}
		}
	}
	log.Printf("history sync %s: stored %d message(s), %d unparseable", data.GetSyncType(), stored, failed)
}

func (b *bridge) storeMessage(evt *events.Message) bool {
	p := ParseEvent(evt)
	chat := b.canonical(p.Chat)
	switch chat.Server {
	case types.DefaultUserServer, types.GroupServer:
	case types.HiddenUserServer:
		// A LID chat whose phone-number mapping is not known yet, which is every chat in
		// the minutes after pairing. Still a DM; stored under the lid rather than dropped,
		// and rows land under the number once the mapping exists.
	default:
		return false // status broadcasts, broadcast lists, newsletters
	}

	if p.Revoke {
		if err := b.store.MarkRevoked(chat.String(), p.ID); err != nil {
			log.Printf("mark revoked %s/%s: %v", chat, p.ID, err)
		}
		return false
	}
	if p.Edited {
		if updated, err := b.store.ApplyEdit(chat.String(), p.ID, p.Text); err != nil {
			log.Printf("apply edit %s/%s: %v", chat, p.ID, err)
			return false
		} else if updated {
			return false
		}
		// the edit target was never seen: fall through and insert what we have
	}
	if p.Text == "" && p.Media == nil {
		return false // reactions, polls, key notices: nothing archivable yet
	}

	kind := "dm"
	if chat.Server == types.GroupServer {
		kind = "group"
	}
	sender := b.canonical(p.Sender)
	senderName := ""
	if !p.FromMe {
		senderName = b.store.ContactName(sender.String())
		if senderName == "" && p.PushName != "" {
			senderName = p.PushName
			_ = b.store.UpsertContact(sender.String(), p.PushName)
		}
	}
	b.recordChat(chat, kind, senderName)

	inserted, err := b.store.Insert(&StoredMessage{
		ChatJID: chat.String(), MsgID: p.ID,
		SenderJID: sender.String(), SenderName: senderName,
		FromMe: p.FromMe, Ts: p.Ts, Text: p.Text, QuotedID: p.QuotedID,
		Media: p.Media, ViewOnce: p.ViewOnce, Ephemeral: p.Ephemeral, Edited: p.Edited,
	})
	if err != nil {
		log.Printf("store %s/%s: %v", chat, p.ID, err)
		return false
	}
	if inserted && p.Media != nil && p.Media.DirectPath != "" {
		b.media.Enqueue(MediaJob{
			ChatJID: chat.String(), MsgID: p.ID,
			MediaType: p.Media.Type, MimeType: p.Media.MimeType, DirectPath: p.Media.DirectPath,
			MediaKey: p.Media.MediaKey, FileSHA: p.Media.FileSHA256, FileEncSHA: p.Media.FileEncSHA256,
			FileLength: p.Media.FileLength,
		})
	}
	if p.QuotedViewOnce != nil && p.QuotedID != "" {
		b.recoverViewOnce(chat, p)
	}
	return inserted
}

// recoverViewOnce archives a view-once whose envelope arrived embedded in a reply's quote:
// the one path that reaches linked devices, since the replying phone builds the quote
// inside the E2E payload where the server cannot strip it.
func (b *bridge) recoverViewOnce(chat types.JID, p *Parsed) {
	media := p.QuotedViewOnce
	sender := ""
	if !p.FromMe {
		sender = b.canonical(p.Sender).String()
	}
	if err := b.store.AttachViewOnceMedia(chat.String(), p.QuotedID, sender,
		b.store.ContactName(sender), p.Ts.Unix(), media); err != nil {
		log.Printf("attach view-once envelope %s/%s: %v", chat, p.QuotedID, err)
		return
	}
	log.Printf("view-once envelope recovered from a quote in %s; downloading", chat)
	b.media.Enqueue(MediaJob{
		ChatJID: chat.String(), MsgID: p.QuotedID,
		MediaType: media.Type, MimeType: media.MimeType, DirectPath: media.DirectPath,
		MediaKey: media.MediaKey, FileSHA: media.FileSHA256, FileEncSHA: media.FileEncSHA256,
		FileLength: media.FileLength,
	})
}

// recordChat keeps the chats table current enough for the archiver to name conversations:
// a DM is named after its contact, a group after its subject, fetched once when unknown.
// The known-name check reads the chats table itself — checking anywhere else re-fetches
// the group over the network for every message, which is what once ground ingest to ~2
// messages a second.
func (b *bridge) recordChat(chat types.JID, kind, senderName string) {
	if b.store.ChatName(chat.String()) != "" {
		return
	}
	if kind == "dm" {
		name := b.store.ContactName(chat.String())
		if name == "" {
			name = senderName
		}
		_ = b.store.UpsertChat(chat.String(), kind, name)
		return
	}
	var name string
	if info, err := b.cli.GetGroupInfo(b.ctx, chat); err == nil {
		name = info.Name
	}
	_ = b.store.UpsertChat(chat.String(), kind, name)
}

// canonical stores phone-number jids: LIDs resolve through the session's mapping, and
// device suffixes drop, so one conversation never appears under two identities.
func (b *bridge) canonical(jid types.JID) types.JID {
	jid = jid.ToNonAD()
	if jid.Server == types.HiddenUserServer {
		if pn, err := b.cli.Store.LIDs.GetPNForLID(b.ctx, jid); err == nil && !pn.IsEmpty() {
			return pn.ToNonAD()
		}
	}
	return jid
}

func (b *bridge) refreshNames() {
	contacts, err := b.cli.Store.Contacts.GetAllContacts(b.ctx)
	if err != nil {
		log.Printf("contact refresh failed: %v", err)
		return
	}
	for jid, info := range contacts {
		_ = b.store.UpsertContact(jid.ToNonAD().String(), bestName(info))
	}
	groups, err := b.cli.GetJoinedGroups(b.ctx)
	if err != nil {
		log.Printf("group refresh failed: %v", err)
		return
	}
	for _, group := range groups {
		_ = b.store.UpsertChat(group.JID.String(), "group", group.Name)
	}
	log.Printf("refreshed %d contact(s), %d group(s)", len(contacts), len(groups))
}

func bestName(info types.ContactInfo) string {
	for _, name := range []string{info.FullName, info.FirstName, info.BusinessName} {
		if name != "" {
			return name
		}
	}
	if info.PushName != "" && info.PushName != "-" {
		return info.PushName
	}
	return ""
}
