package main

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"log"
	"os"
	"path/filepath"
	"strings"

	"github.com/mdp/qrterminal/v3"
	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/proto/waCompanionReg"
	"go.mau.fi/whatsmeow/store"
	"go.mau.fi/whatsmeow/store/sqlstore"
	waLog "go.mau.fi/whatsmeow/util/log"
)

func newClient(ctx context.Context, storeDir string) (*whatsmeow.Client, error) {
	if err := os.MkdirAll(storeDir, 0o700); err != nil {
		return nil, err
	}
	store.DeviceProps.PlatformType = waCompanionReg.DeviceProps_DESKTOP.Enum()
	store.SetOSInfo("wabridge", [3]uint32{0, 1, 0})

	sessionPath := filepath.Join(storeDir, "session.db")
	container, err := sqlstore.New(ctx, "sqlite3", "file:"+sessionPath+"?_foreign_keys=on", waLog.Stdout("session", "ERROR", true))
	if err != nil {
		return nil, fmt.Errorf("open session store: %w", err)
	}
	device, err := container.GetFirstDevice(ctx)
	if errors.Is(err, sql.ErrNoRows) {
		device = container.NewDevice()
	} else if err != nil {
		return nil, fmt.Errorf("load device: %w", err)
	}
	level := os.Getenv("WABRIDGE_LOG")
	if level == "" {
		level = "WARN"
	}
	return whatsmeow.NewClient(device, waLog.Stdout("client", level, true)), nil
}

// pairLoop drives the QR channel until pairing completes: QR by default (rendered to the
// terminal and, when qrFile is set, written there as plain text for the web UI), a pairing
// code when a phone number is given. The caller's connection stays up on success.
func pairLoop(ctx context.Context, cli *whatsmeow.Client, qrChan <-chan whatsmeow.QRChannelItem, phone, qrFile string) error {
	if qrFile != "" {
		defer os.Remove(qrFile)
	}
	codeRequested := false
	for evt := range qrChan {
		switch {
		case evt.Event == whatsmeow.QRChannelEventCode:
			if phone != "" {
				if codeRequested {
					continue
				}
				codeRequested = true
				code, err := cli.PairPhone(ctx, phone, true, whatsmeow.PairClientChrome, "Chrome (Linux)")
				if err != nil {
					return fmt.Errorf("request pairing code: %w", err)
				}
				fmt.Printf("enter this code on the phone: %s\n", code)
			} else {
				if qrFile != "" {
					writeQRFile(qrFile, evt.Code)
				}
				qrterminal.GenerateHalfBlock(evt.Code, qrterminal.M, os.Stdout)
				fmt.Println("scan with WhatsApp: Settings > Linked Devices > Link a Device")
			}
		case evt.Event == whatsmeow.QRChannelSuccess.Event:
			log.Printf("paired as %s", cli.Store.ID)
			return nil
		case evt.Event == whatsmeow.QRChannelEventError:
			return fmt.Errorf("pairing failed: %w", evt.Error)
		default:
			return fmt.Errorf("pairing ended: %s", evt.Event)
		}
	}
	return errors.New("qr channel closed before pairing completed")
}

// Plain block characters, no ANSI escapes: the file is rendered inside a <pre>, not a
// terminal. Written atomically so a reader never sees half a code.
func writeQRFile(path, code string) {
	var buf strings.Builder
	qrterminal.GenerateWithConfig(code, qrterminal.Config{
		Level: qrterminal.M, Writer: &buf, BlackChar: "██", WhiteChar: "  ", QuietZone: 2,
	})
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, []byte(buf.String()), 0o600); err == nil {
		_ = os.Rename(tmp, path)
	}
}
