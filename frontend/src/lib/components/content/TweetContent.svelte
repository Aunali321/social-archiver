<script lang="ts">
	import Repeat2 from '@lucide/svelte/icons/repeat-2';
	import ItemText from '../ItemText.svelte';
	import ContextCard from '../ContextCard.svelte';
	import MediaStrip from '../MediaStrip.svelte';
	import ThreadExpander from '../ThreadExpander.svelte';
	import type { Item } from '$lib/api';

	/** A tweet the way twitter shows one: the tweet it answers above it, the tweet it quotes
	 * below it, a retweet unwrapped to the original, a thread expandable in place. */
	interface Props {
		item: Item;
		snippet?: string | null;
	}

	let { item, snippet = null }: Props = $props();

	const retweeted = $derived(item.context?.retweeted ?? null);
	const parent = $derived(item.context?.parent ?? null);
	const quoted = $derived(item.context?.quoted ?? null);
	// The body of a bare retweet is the original; showing both repeats the same text
	const body = $derived(item.is_retweet && retweeted ? null : item);
</script>

{#if item.is_retweet && retweeted}
	<p class="mb-1 inline-flex items-center gap-1.5 text-label text-on-surface-variant">
		<Repeat2 size={13} /> retweeted {retweeted.author_username}
	</p>
	<ContextCard item={retweeted} />
{/if}

{#if parent}
	<div class="mb-1">
		<ContextCard item={parent} label="replying to" />
	</div>
{:else if item.in_reply_to_status_id}
	<p class="mb-1 text-label text-on-surface-variant italic">
		replying to a tweet that isn't archived
	</p>
{/if}

{#if body}
	<ItemText text={body.text} {snippet} clamp={12} />
	{#if body.media.length}
		<div class="mt-2"><MediaStrip item={body} /></div>
	{/if}
{/if}

{#if quoted}
	<div class="mt-2"><ContextCard item={quoted} label="quoting" /></div>
{/if}

{#if item.in_thread && item.thread_root_id}
	<div class="mt-2"><ThreadExpander {item} /></div>
{/if}
