<script lang="ts">
	import CornerDownRight from '@lucide/svelte/icons/corner-down-right';
	import ItemText from '../ItemText.svelte';
	import ContextCard from '../ContextCard.svelte';
	import MediaStrip from '../MediaStrip.svelte';
	import type { Item } from '$lib/api';

	/** A post gets its title back as a heading; a comment gets the submission it sits under,
	 * and its parent comment when the reply chain is archived. */
	interface Props {
		item: Item;
		snippet?: string | null;
	}

	let { item, snippet = null }: Props = $props();

	const isComment = $derived(item.item_id.startsWith('t1_'));
	const submission = $derived(item.context?.submission ?? null);
	const parent = $derived(item.context?.parent ?? null);
	// The parent comment is more specific context than the submission; show the closest one
	const above = $derived(parent ?? submission);
	const linkDomain = $derived.by(() => {
		if (!item.link_url) return null;
		try {
			return new URL(item.link_url).hostname.replace(/^www\./, '');
		} catch {
			return null;
		}
	});
</script>

{#if isComment && above}
	<div class="mb-1">
		<ContextCard item={above} label={parent ? 'replying to' : 'on'} />
	</div>
{/if}

{#if item.title}
	<h3 class="text-body-lg font-medium text-on-surface">{item.title}</h3>
{/if}

<div class={isComment && above ? 'flex gap-2' : ''}>
	{#if isComment && above}
		<CornerDownRight size={14} class="mt-0.5 shrink-0 text-on-surface-variant" />
	{/if}
	<div class="min-w-0 flex-1">
		<ItemText text={item.text} {snippet} clamp={8} class={item.title ? 'mt-1' : ''} />
	</div>
</div>

{#if linkDomain}
	<a
		href={item.link_url}
		target="_blank"
		rel="noopener noreferrer"
		onclick={(event) => event.stopPropagation()}
		class="mt-2 inline-block rounded-full bg-surface-container-high px-3 py-1 text-label text-primary"
	>
		{linkDomain} ↗
	</a>
{/if}

{#if item.media.length}
	<div class="mt-2"><MediaStrip {item} /></div>
{/if}
