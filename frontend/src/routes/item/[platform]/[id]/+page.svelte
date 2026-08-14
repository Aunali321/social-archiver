<script lang="ts">
	import { page } from '$app/state';
	import ArrowLeft from '@lucide/svelte/icons/arrow-left';
	import ExternalLink from '@lucide/svelte/icons/external-link';
	import CircleAlert from '@lucide/svelte/icons/circle-alert';
	import DownloadCloud from '@lucide/svelte/icons/cloud-download';
	import GitBranch from '@lucide/svelte/icons/git-branch';
	import Heart from '@lucide/svelte/icons/heart';
	import MessageSquare from '@lucide/svelte/icons/message-square';
	import Repeat2 from '@lucide/svelte/icons/repeat-2';
	import Quote from '@lucide/svelte/icons/quote';
	import Eye from '@lucide/svelte/icons/eye';
	import Bookmark from '@lucide/svelte/icons/bookmark';
	import ScanEye from '@lucide/svelte/icons/scan-eye';
	import { api, ApiError, type Conversation, type ConversationNode, type ItemDetail } from '$lib/api';
	import { formatCount, formatDateTime, platformLabel } from '$lib/format';
	import Avatar from '$lib/components/Avatar.svelte';
	import PlatformBadge from '$lib/components/PlatformBadge.svelte';
	import MediaStrip from '$lib/components/MediaStrip.svelte';
	import ItemCard from '$lib/components/ItemCard.svelte';
	import ConversationReply from '$lib/components/ConversationReply.svelte';
	import Chip from '$lib/components/Chip.svelte';
	import Card from '$lib/components/Card.svelte';
	import Button from '$lib/components/Button.svelte';
	import Skeleton from '$lib/components/Skeleton.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import { toast } from '$lib/components/snackbar.svelte';

	let detail = $state<ItemDetail | null>(null);
	let conversation = $state<Conversation | null>(null);
	let likedOnly = $state(false);
	let error = $state<string | null>(null);
	let recovering = $state(false);

	const CONVERSATION_PLATFORMS = ['twitter', 'reddit'];

	const platform = $derived(page.params.platform!);
	const itemId = $derived(decodeURIComponent(page.params.id!));

	$effect(() => {
		detail = null;
		conversation = null;
		error = null;
		api
			.item(platform, itemId)
			.then((d) => (detail = d))
			.catch((e) => (error = e instanceof Error ? e.message : String(e)));
		if (CONVERSATION_PLATFORMS.includes(platform)) {
			api.conversation(platform, itemId).then((c) => (conversation = c));
		}
	});

	/** Liked-only keeps seeds plus the spine needed to reach a deeper seed. */
	function prune(nodes: ConversationNode[]): ConversationNode[] {
		return nodes
			.map((node) => ({ ...node, replies: prune(node.replies) }))
			.filter((node) => node.item.is_seed || node.replies.length > 0);
	}

	const visibleReplies = $derived(
		conversation ? (likedOnly ? prune(conversation.replies) : conversation.replies) : []
	);
	const hiddenCount = $derived.by(() => {
		if (!conversation || !likedOnly) return 0;
		const count = (nodes: ConversationNode[]): number =>
			nodes.reduce((sum, node) => sum + 1 + count(node.replies), 0);
		return count(conversation.replies) - count(visibleReplies);
	});

	const item = $derived(detail?.item);
	const missingMedia = $derived(item ? item.media.filter((m) => !m.available).length : 0);

	const counts = $derived(
		item
			? ([
					[Heart, item.like_count],
					[MessageSquare, item.reply_count],
					[Repeat2, item.retweet_count],
					[Quote, item.quote_count],
					[Bookmark, item.bookmark_count],
					[Eye, item.view_count]
				] as const)
			: []
	);

	async function recover() {
		if (!item) return;
		recovering = true;
		try {
			const refreshed = await api.recoverMedia(platform, itemId);
			detail = { ...detail!, item: refreshed };
			toast('Media re-downloaded');
		} catch (e) {
			toast(e instanceof ApiError ? e.message : 'Recovery failed', { error: true });
		} finally {
			recovering = false;
		}
	}
</script>

<svelte:head><title>{item ? `@${item.author_username}` : 'Item'} · Archive</title></svelte:head>

<div class="mx-auto max-w-2xl px-4 pt-4">
	<button
		onclick={() => history.back()}
		class="state-layer mb-3 inline-flex h-10 cursor-pointer items-center gap-2 rounded-full px-3 text-label-lg text-on-surface-variant"
	>
		<ArrowLeft size={18} /> Back
	</button>

	{#if error}
		<EmptyState title="Couldn't load this item" detail={error} error>
			{#snippet icon()}<CircleAlert size={28} />{/snippet}
		</EmptyState>
	{:else if !detail || !item}
		<div class="rounded-md bg-surface-container-low p-5">
			<div class="flex items-center gap-3">
				<Skeleton class="size-7 rounded-full" />
				<Skeleton class="h-4 w-48" />
			</div>
			<Skeleton class="mt-5 h-4 w-full" />
			<Skeleton class="mt-2 h-4 w-full" />
			<Skeleton class="mt-2 h-4 w-3/5" />
		</div>
	{:else}
		{#if conversation?.missing_parent}
			<p class="mb-2 text-label text-on-surface-variant italic">
				earlier tweets in this conversation aren't archived
			</p>
		{/if}
		{#if conversation && conversation.ancestors.length}
			<div class="mb-2 flex flex-col gap-1 border-l-2 border-outline-variant pl-2">
				{#each conversation.ancestors as ancestor (ancestor.item_id)}
					<ConversationReply node={{ item: ancestor, replies: [] }} />
				{/each}
			</div>
		{/if}
		<article class="rounded-md bg-surface-container-low p-5">
			<header class="flex items-center gap-3">
				<span class="relative shrink-0">
					<Avatar name={item.author_username} size={44} />
					<span class="absolute -right-1.5 -bottom-1.5 scale-[0.68]">
						<PlatformBadge platform={item.platform} />
					</span>
				</span>
				<div class="min-w-0 flex-1">
					<p class="text-title text-on-surface">
						{item.author_username}
						{#if item.shared_by_username}
							<span class="text-body text-on-surface-variant">
								· shared by {item.shared_by_username}</span
							>
						{/if}
					</p>
					<p class="text-label text-on-surface-variant">
						{formatDateTime(item.created_at)}
						{#if item.subreddit}· r/{item.subreddit}{/if}
						{#if item.chat_name}· {item.chat_name}{/if}
					</p>
				</div>
				<a
					href={item.post_url}
					target="_blank"
					rel="noopener noreferrer"
					class="state-layer flex size-10 items-center justify-center rounded-full text-on-surface-variant"
					title="Open original"
				>
					<ExternalLink size={18} />
				</a>
			</header>

			{#if item.text}
				<p class="mt-4 text-body-lg break-words whitespace-pre-line text-on-surface">{item.text}</p>
			{/if}

			{#if item.link_url}
				<a
					href={item.link_url}
					target="_blank"
					rel="noopener noreferrer"
					class="mt-3 block truncate rounded-sm bg-surface-container px-3 py-2 text-body text-primary"
				>
					{item.link_url}
				</a>
			{/if}

			{#if item.media.length}
				<div class="mt-4"><MediaStrip {item} large /></div>
				{#if missingMedia > 0}
					<div class="mt-3 flex items-center gap-3 rounded-sm bg-surface-container px-3 py-2">
						<p class="flex-1 text-label text-on-surface-variant">
							{missingMedia} of {item.media.length} file(s) not on disk (cleaned up after upload)
						</p>
						{#if item.platform !== 'whatsapp'}
							<Button variant="tonal" disabled={recovering} onclick={recover}>
								<DownloadCloud size={16} />
								{recovering ? 'Fetching…' : 'Re-download'}
							</Button>
						{/if}
					</div>
				{/if}
			{/if}

			{#if counts.some(([, n]) => n != null && n > 0)}
				<div class="mt-4 flex flex-wrap gap-4 text-label-lg text-on-surface-variant">
					{#each counts as [Icon, count], i (i)}
						{#if count != null && count > 0}
							<span class="inline-flex items-center gap-1.5"><Icon size={15} />{formatCount(count)}</span>
						{/if}
					{/each}
				</div>
			{/if}

			{#if item.vlm_description}
				<details class="mt-4 rounded-sm bg-surface-container p-3">
					<summary
						class="flex cursor-pointer items-center gap-2 text-label-lg text-on-surface-variant"
					>
						<ScanEye size={16} /> Media description (AI)
					</summary>
					<p class="mt-2 text-body break-words whitespace-pre-line text-on-surface-variant">
						{item.vlm_description}
					</p>
				</details>
			{/if}

			<!-- Provenance -->
			<footer class="mt-5 flex flex-wrap gap-1.5 border-t border-outline-variant pt-4">
				<span class="rounded-full bg-secondary-container px-3 py-1 text-label text-on-secondary-container">
					{platformLabel(item.platform)}
				</span>
				{#each detail.categories.length ? detail.categories : [item.category] as cat (cat)}
					<span class="rounded-full bg-surface-container-high px-3 py-1 text-label text-on-surface-variant">
						{cat}
					</span>
				{/each}
				{#if item.origin}
					<span
						class="rounded-full bg-tertiary-container px-3 py-1 text-label text-on-tertiary-container"
						title="How this item entered the archive"
					>
						via {item.origin}
					</span>
				{/if}
				{#each detail.collections as collection (collection)}
					<span class="rounded-full bg-surface-container-high px-3 py-1 text-label text-on-surface-variant">
						📁 {collection}
					</span>
				{/each}
				{#if item.source_target}
					<span class="rounded-full bg-surface-container-high px-3 py-1 text-label text-on-surface-variant">
						source: {item.source_target}
					</span>
				{/if}
			</footer>

			{#if item.thread_root_id && !conversation}
				<a
					href="/thread/{item.platform}/{encodeURIComponent(item.thread_root_id)}"
					class="mt-4 inline-flex items-center gap-2 text-label-lg text-primary"
				>
					<GitBranch size={16} /> View full {item.platform === 'whatsapp'
						? 'chat day'
						: 'thread'}
				</a>
			{/if}
		</article>

		<!-- Graph neighbours -->
		{#each [['In reply to', conversation ? null : detail.parent], ['Quoted', detail.quoted], ['Retweeted', detail.retweeted], ['Discovered via', detail.discovered_via]] as const as [label, neighbour] (label)}
			{#if neighbour}
				<section class="mt-4">
					<h2 class="mb-2 text-label-lg text-on-surface-variant">{label}</h2>
					<ItemCard item={neighbour} />
				</section>
			{/if}
		{/each}

		{#if conversation}
			{#if conversation.replies.length}
				<section class="mt-4">
					<div class="mb-2 flex items-center gap-2">
						<h2 class="flex-1 text-label-lg text-on-surface-variant">Conversation</h2>
						<Chip selected={!likedOnly} onclick={() => (likedOnly = false)}>Everything</Chip>
						<Chip selected={likedOnly} onclick={() => (likedOnly = true)}>Liked only</Chip>
					</div>
					<div class="flex flex-col gap-1">
						{#each visibleReplies as node (node.item.item_id)}
							<ConversationReply {node} />
						{/each}
					</div>
					{#if likedOnly && hiddenCount > 0}
						<p class="mt-2 text-label text-on-surface-variant">
							{hiddenCount} context repl{hiddenCount === 1 ? 'y' : 'ies'} hidden — the author's own
							replies and their parents, archived for completeness
						</p>
					{/if}
				</section>
			{/if}
		{:else if detail.replies.length}
			<section class="mt-4">
				<h2 class="mb-2 text-label-lg text-on-surface-variant">
					Archived replies ({detail.replies.length})
				</h2>
				<div class="flex flex-col gap-3">
					{#each detail.replies as reply (reply.item_id)}
						<ItemCard item={reply} />
					{/each}
				</div>
			</section>
		{/if}
	{/if}
	<div class="h-8"></div>
</div>
