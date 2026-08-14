<script lang="ts">
	import { untrack } from 'svelte';
	import { page } from '$app/state';
	import ArrowLeft from '@lucide/svelte/icons/arrow-left';
	import CircleAlert from '@lucide/svelte/icons/circle-alert';
	import { api, type Item } from '$lib/api';
	import { authorHue, dayKey, formatDay, formatTime } from '$lib/format';
	import MediaStrip from '$lib/components/MediaStrip.svelte';
	import Button from '$lib/components/Button.svelte';
	import Skeleton from '$lib/components/Skeleton.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';

	let messages: Item[] = $state([]); // chronological
	let cursor = $state<string | null>(null);
	let exhausted = $state(false);
	let loading = $state(false);
	let error = $state<string | null>(null);
	let title = $state('');

	const platform = $derived(page.params.platform!);
	const chatId = $derived(decodeURIComponent(page.params.id!));

	$effect(() => {
		// Reading chatId subscribes this effect to route changes only; the loader's own
		// state reads must stay untracked or its loading flag would re-trigger the effect.
		title = chatId.split('@')[0];
		untrack(() => {
			messages = [];
			cursor = null;
			exhausted = false;
			error = null;
			loadOlder(true);
		});
	});

	async function loadOlder(first = false) {
		if (loading || exhausted) return;
		loading = true;
		try {
			const result = await api.items({ platforms: platform, chat: chatId }, cursor, 60);
			// API returns newest-first; the transcript reads oldest-first
			messages.unshift(...result.items.slice().reverse());
			cursor = result.next_cursor;
			exhausted = result.next_cursor == null;
			const named = result.items.find((item) => item.chat_name);
			if (named?.chat_name) title = named.chat_name;
			if (first) requestAnimationFrame(() => window.scrollTo(0, document.body.scrollHeight));
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			loading = false;
		}
	}

	const isMine = (message: Item) => message.author_username === 'me';
	const isGroup = $derived(messages.some((m) => m.category === 'group'));


</script>

<svelte:head><title>{title} · Archive</title></svelte:head>

<div class="mx-auto flex min-h-dvh max-w-2xl flex-col px-4">
	<header
		class="sticky top-0 z-10 -mx-4 flex items-center gap-2 bg-surface/95 px-4 py-3 backdrop-blur"
	>
		<a
			href="/chats"
			class="state-layer flex size-10 items-center justify-center rounded-full text-on-surface-variant"
			aria-label="Back to chats"
		>
			<ArrowLeft size={20} />
		</a>
		<h1 class="min-w-0 flex-1 truncate text-title-lg text-on-surface">{title}</h1>
	</header>

	{#if error}
		<EmptyState title="Couldn't load this chat" detail={error} error>
			{#snippet icon()}<CircleAlert size={28} />{/snippet}
		</EmptyState>
	{:else if messages.length === 0 && loading}
		<div class="flex flex-col gap-2 py-4">
			{#each [56, 72, 48, 64, 40] as width, i (i)}
				<div
					class="shimmer h-10 rounded-lg {i % 2 ? 'self-end' : 'self-start'}"
					style="width: {width}%"
					aria-hidden="true"
				></div>
			{/each}
		</div>
	{:else}
		{#if !exhausted && messages.length}
			<div class="flex justify-center py-3">
				<Button variant="text" disabled={loading} onclick={() => loadOlder()}>
					{loading ? 'Loading…' : 'Load older messages'}
				</Button>
			</div>
		{/if}

		<div class="flex flex-col gap-1 pb-6">
			{#each messages as message, index (message.item_id)}
				{@const day = dayKey(message.created_at)}
				{#if index === 0 || day !== dayKey(messages[index - 1].created_at)}
					<div class="flex items-center justify-center py-3">
						<a
							href="/thread/{platform}/{encodeURIComponent(`${chatId}:${day}`)}"
							class="rounded-full bg-surface-container-high px-3 py-1 text-label text-on-surface-variant"
						>
							{formatDay(day)}
						</a>
					</div>
				{/if}
				{@const mine = isMine(message)}
				<div class="flex {mine ? 'justify-end' : 'justify-start'}">
					<div
						class="max-w-[82%] rounded-lg px-3 py-2 {mine
							? 'rounded-br-xs bg-primary-container text-on-primary-container'
							: 'rounded-bl-xs bg-surface-container-high text-on-surface'}"
					>
						{#if isGroup && !mine && (index === 0 || messages[index - 1].author_username !== message.author_username)}
							<p
								class="text-label font-medium"
								style="color: oklch(55% 0.1 {authorHue(message.author_username)})"
							>
								{message.author_username}
							</p>
						{/if}
						{#if message.media.length}
							<div class="my-1"><MediaStrip item={message} /></div>
						{/if}
						{#if message.text}
							<p class="text-body break-words whitespace-pre-line">{message.text}</p>
						{/if}
						<time class="mt-0.5 block text-right text-[0.65rem] opacity-60">
							{formatTime(message.created_at)}
						</time>
					</div>
				</div>
			{/each}
		</div>
	{/if}
</div>
