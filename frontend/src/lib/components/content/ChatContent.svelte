<script lang="ts">
	import ItemText from '../ItemText.svelte';
	import ContextCard from '../ContextCard.svelte';
	import MediaStrip from '../MediaStrip.svelte';
	import type { Item } from '$lib/api';

	/** A chat message with the message it replies to, when that reply is archived. */
	interface Props {
		item: Item;
		snippet?: string | null;
	}

	let { item, snippet = null }: Props = $props();

	const parent = $derived(item.context?.parent ?? null);
</script>

{#if parent}
	<div class="mb-1"><ContextCard item={parent} label="replying to" /></div>
{/if}

<ItemText text={item.text} {snippet} clamp={8} />

{#if item.media.length}
	<div class="mt-2"><MediaStrip {item} /></div>
{/if}
