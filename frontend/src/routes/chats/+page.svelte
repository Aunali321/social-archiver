<script lang="ts">
	import MessagesSquare from '@lucide/svelte/icons/messages-square';
	import CircleAlert from '@lucide/svelte/icons/circle-alert';
	import Users from '@lucide/svelte/icons/users';
	import User from '@lucide/svelte/icons/user';
	import SearchIcon from '@lucide/svelte/icons/search';
	import { api, type Chat } from '$lib/api';
	import { formatCount, formatDate } from '$lib/format';
	import TextField from '$lib/components/TextField.svelte';
	import Skeleton from '$lib/components/Skeleton.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';

	let chats = $state<Chat[] | null>(null);
	let error = $state<string | null>(null);
	let filter = $state('');

	$effect(() => {
		api
			.chats('whatsapp')
			.then((result) => (chats = result))
			.catch((e) => (error = e instanceof Error ? e.message : String(e)));
	});

	const visible = $derived(
		(chats ?? []).filter((chat) => {
			const needle = filter.trim().toLowerCase();
			if (!needle) return true;
			return (chat.name ?? chat.chat_id).toLowerCase().includes(needle);
		})
	);

	function displayName(chat: Chat): string {
		return chat.name || chat.chat_id.split('@')[0];
	}
</script>

<svelte:head><title>Chats · Archive</title></svelte:head>

<div class="mx-auto max-w-2xl px-4 pt-6">
	<h1 class="mb-4 text-headline text-on-surface">Chats</h1>

	{#if error}
		<EmptyState title="Couldn't load chats" detail={error} error>
			{#snippet icon()}<CircleAlert size={28} />{/snippet}
		</EmptyState>
	{:else if chats == null}
		<div class="flex flex-col gap-1">
			{#each Array(8), i (i)}
				<div class="flex items-center gap-3 rounded-md p-3">
					<Skeleton class="size-11 rounded-full" />
					<div class="flex-1">
						<Skeleton class="h-3.5 w-36" />
						<Skeleton class="mt-2 h-3 w-3/4" />
					</div>
				</div>
			{/each}
		</div>
	{:else if chats.length === 0}
		<EmptyState
			title="No chats archived"
			detail="WhatsApp conversations appear here once the bridge has synced."
		>
			{#snippet icon()}<MessagesSquare size={28} />{/snippet}
		</EmptyState>
	{:else}
		<TextField label="Filter chats" bind:value={filter} type="search" class="mb-3">
			{#snippet leading()}<SearchIcon size={18} />{/snippet}
		</TextField>
		<div class="flex flex-col">
			{#each visible as chat (chat.chat_id)}
				<a
					href="/chats/whatsapp/{encodeURIComponent(chat.chat_id)}"
					class="state-layer flex items-center gap-3 rounded-md p-3"
				>
					<span
						class="flex size-11 shrink-0 items-center justify-center rounded-full bg-secondary-container text-on-secondary-container"
					>
						{#if chat.category === 'group'}<Users size={20} />{:else}<User size={20} />{/if}
					</span>
					<div class="min-w-0 flex-1">
						<div class="flex items-baseline justify-between gap-2">
							<p class="truncate text-title text-on-surface">{displayName(chat)}</p>
							<time class="shrink-0 text-label text-on-surface-variant">
								{formatDate(chat.last_at)}
							</time>
						</div>
						<p class="truncate text-body text-on-surface-variant">
							{#if chat.last_author && chat.last_author !== 'me'}{chat.last_author}: {/if}
							{chat.last_text || '(media)'}
						</p>
					</div>
					<span class="shrink-0 rounded-full bg-surface-container-high px-2 py-0.5 text-label text-on-surface-variant">
						{formatCount(chat.message_count)}
					</span>
				</a>
			{/each}
			{#if visible.length === 0}
				<p class="py-8 text-center text-body text-on-surface-variant">No chat matches “{filter}”.</p>
			{/if}
		</div>
	{/if}
	<div class="h-8"></div>
</div>
