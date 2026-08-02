package main

import (
	"context"
	"errors"
	"log"
	"runtime/debug"
	"strings"

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
}

// runSync is the daemon: connect, mirror every message and history-sync payload into the
// store, download media eagerly, and keep running until the context ends. It never sends
// anything and never marks anything read.
func runSync(ctx context.Context, cli *whatsmeow.Client, store *Store) error {
	if cli.Store.ID == nil {
		return errors.New("not paired; run `wabridge auth` first")
	}
	b := &bridge{ctx: ctx, cli: cli, store: store, media: newMediaPool(ctx, cli, store)}

	// Manual mode delivers history blobs as notifications instead of downloading them
	// behind our back, so ingest controls the timing and sees every payload.
	cli.ManualHistorySyncDownload = true
	cli.AddEventHandler(b.handle)
	if err := cli.Connect(); err != nil {
		return err
	}
	log.Printf("syncing as %s into %s", cli.Store.ID, store.Dir)
	b.media.EnqueueBacklog()

	<-ctx.Done()
	cli.Disconnect()
	return nil
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
			b.historyNotification(notif)
			return
		}
		b.storeMessage(v)
	case *events.HistorySync:
		b.ingestHistory(v.Data)
	case *events.Connected:
		log.Printf("connected")
		if !b.refreshed {
			b.refreshed = true
			go b.refreshNames()
		}
	case *events.LoggedOut:
		log.Printf("logged out by the phone; delete session.db and pair again")
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
	if chat.Server != types.DefaultUserServer && chat.Server != types.GroupServer {
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
	return inserted
}

// recordChat keeps the chats table current enough for the archiver to name conversations:
// a DM is named after its contact, a group after its subject, fetched once when unknown.
func (b *bridge) recordChat(chat types.JID, kind, senderName string) {
	if kind == "dm" {
		name := b.store.ContactName(chat.String())
		if name == "" {
			name = senderName
		}
		_ = b.store.UpsertChat(chat.String(), kind, name)
		return
	}
	if b.store.ContactName(chat.String()) != "" {
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
