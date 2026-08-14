<script lang="ts">
	import ImageIcon from '@lucide/svelte/icons/image';
	import Avatar from './Avatar.svelte';
	import ItemText from './ItemText.svelte';
	import MediaStrip from './MediaStrip.svelte';
	import type { Item } from '$lib/api';
	import { formatDate } from '$lib/format';

	/** A graph neighbour embedded compactly inside a card: the quoted tweet, the message
	 * being replied to. Enough to make the main item legible, one click from the full view. */
	interface Props {
		item: Item;
		label?: string | null;
	}

	let { item, label = null }: Props = $props();

	// Media renders inline when it's on disk; a bare count is the fallback, not the default
	const hasLocalMedia = $derived(item.media.some((media) => media.available));
</script>

<a
	href="/item/{item.platform}/{encodeURIComponent(item.item_id)}"
	class="state-layer block rounded-sm border border-outline-variant px-3 py-2"
	onclick={(event) => event.stopPropagation()}
>
	<p class="flex items-center gap-2 text-label text-on-surface-variant">
		{#if label}<span class="shrink-0 font-medium">{label}</span>{/if}
		<Avatar name={item.author_username} size={20} />
		<span class="truncate font-medium text-on-surface">{item.author_username}</span>
		<span class="ml-auto shrink-0">{formatDate(item.created_at)}</span>
	</p>
	{#if item.title}
		<p class="mt-0.5 truncate text-body font-medium text-on-surface">{item.title}</p>
	{/if}
	<ItemText text={item.text} clamp={3} class="mt-0.5 !text-on-surface-variant" />
	{#if hasLocalMedia}
		<div class="mt-1.5"><MediaStrip {item} /></div>
	{:else if item.media.length}
		<p class="mt-1 inline-flex items-center gap-1 text-label text-on-surface-variant">
			<ImageIcon size={12} />{item.media.length}
		</p>
	{/if}
</a>
