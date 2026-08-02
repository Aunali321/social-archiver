package main

import (
	"context"
	"flag"
	"fmt"
	"log"
	"os"
	"os/signal"
	"syscall"
)

func main() {
	storeDir := flag.String("store", "wabridge-store", "directory holding session.db, wabridge.db and media/")
	phone := flag.String("phone", "", "pair with a phone-number code instead of a QR (auth only)")
	qrFile := flag.String("qr-file", "", "also write each QR to this file as plain text (auth only)")
	flag.Parse()

	command := flag.Arg(0)
	if command != "auth" && command != "sync" {
		fmt.Fprintln(os.Stderr, "usage: wabridge [-store DIR] [-phone +NUMBER] auth|sync")
		os.Exit(2)
	}

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	cli, err := newClient(ctx, *storeDir)
	if err != nil {
		log.Fatal(err)
	}

	switch command {
	case "auth":
		err = auth(ctx, cli, *phone, *qrFile)
	case "sync":
		var store *Store
		if store, err = OpenStore(*storeDir); err == nil {
			defer store.Close()
			err = runSync(ctx, cli, store)
		}
	}
	if err != nil {
		log.Fatal(err)
	}
}
