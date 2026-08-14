<script lang="ts">
	import Heart from '@lucide/svelte/icons/heart';
	import MessageSquare from '@lucide/svelte/icons/message-square';
	import Repeat2 from '@lucide/svelte/icons/repeat-2';
	import Eye from '@lucide/svelte/icons/eye';
	import type { Item } from '$lib/api';
	import { formatCount } from '$lib/format';

	/** The platform's own numbers for a post, shown wherever the post is readable. */
	interface Props {
		item: Item;
		class?: string;
	}

	let { item, class: className = '' }: Props = $props();

	const counts = $derived(
		(
			[
				[MessageSquare, item.reply_count],
				[Repeat2, item.retweet_count],
				[Heart, item.like_count],
				[Eye, item.view_count]
			] as const
		).filter(([, count]) => count != null && count > 0)
	);
</script>

{#if counts.length}
	<div class="flex gap-4 text-label text-on-surface-variant {className}">
		{#each counts as [Icon, count], i (i)}
			<span class="inline-flex items-center gap-1"><Icon size={13} />{formatCount(count)}</span>
		{/each}
	</div>
{/if}
