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
	pair := flag.Bool("pair", false, "pair first when no session exists, then keep syncing")
	phone := flag.String("phone", "", "pair with a phone-number code instead of a QR")
	qrFile := flag.String("qr-file", "", "also write each QR to this file as plain text")
	flag.Parse()

	if flag.Arg(0) != "sync" {
		fmt.Fprintln(os.Stderr, "usage: wabridge [-store DIR] [-pair] [-phone +NUMBER] [-qr-file PATH] sync")
		os.Exit(2)
	}

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	cli, err := newClient(ctx, *storeDir)
	if err != nil {
		log.Fatal(err)
	}
	store, err := OpenStore(*storeDir)
	if err != nil {
		log.Fatal(err)
	}
	defer store.Close()

	if err := runSync(ctx, cli, store, *pair, *phone, *qrFile); err != nil {
		log.Fatal(err)
	}
}
