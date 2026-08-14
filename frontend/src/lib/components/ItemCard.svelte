<script lang="ts">
	import Repeat2 from '@lucide/svelte/icons/repeat-2';
	import Avatar from './Avatar.svelte';
	import EngagementRow from './EngagementRow.svelte';
	import PlatformBadge from './PlatformBadge.svelte';
	import ItemText from './ItemText.svelte';
	import MediaStrip from './MediaStrip.svelte';
	import TweetContent from './content/TweetContent.svelte';
	import RedditContent from './content/RedditContent.svelte';
	import ChatContent from './content/ChatContent.svelte';
	import type { Item } from '$lib/api';
	import { formatDate } from '$lib/format';

	/** One feed entry: a shared shell (author, place, date, counts) around platform-specific
	 * content, because a tweet, a reddit comment and a chat message read differently. */
	interface Props {
		item: Item;
		snippet?: string | null;
	}

	let { item, snippet = null }: Props = $props();

	const href = $derived(`/item/${item.platform}/${encodeURIComponent(item.item_id)}`);
	const where = $derived(
		item.subreddit ? `r/${item.subreddit}` : (item.chat_name ?? item.collection_name ?? item.category)
	);
	function open(event: MouseEvent) {
		// The card navigates, but embedded links (context cards, external links) win
		if (!(event.target as HTMLElement).closest('a, button')) {
			location.assign(href);
		}
	}
</script>

<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_noninteractive_element_interactions -->
<article
	onclick={open}
	class="state-layer cursor-pointer rounded-md bg-surface-container-low px-4 py-3 transition-shadow hover:shadow-e1"
>
	<header class="mb-2 flex items-center gap-2.5">
		<span class="relative shrink-0">
			<Avatar name={item.author_username} size={40} />
			<span class="absolute -right-1.5 -bottom-1.5 scale-[0.68]">
				<PlatformBadge platform={item.platform} />
			</span>
		</span>
		<div class="min-w-0 flex-1 leading-tight">
			<a href={href} class="block truncate text-label-lg text-on-surface hover:underline">
				{item.author_username}
			</a>
			<p class="truncate text-label text-on-surface-variant">{where}</p>
		</div>
		<time class="shrink-0 text-label text-on-surface-variant">{formatDate(item.created_at)}</time>
	</header>

	{#if item.platform === 'twitter'}
		<TweetContent {item} {snippet} />
	{:else if item.platform === 'reddit'}
		<RedditContent {item} {snippet} />
	{:else if item.platform === 'whatsapp'}
		<ChatContent {item} {snippet} />
	{:else}
		<ItemText text={item.text} {snippet} clamp={10} />
		{#if item.media.length}
			<div class="mt-2"><MediaStrip {item} /></div>
		{/if}
	{/if}

	<EngagementRow {item} class="mt-2" />
</article>
