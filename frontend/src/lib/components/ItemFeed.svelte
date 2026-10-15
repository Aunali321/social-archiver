<script lang="ts">
	import { onMount } from 'svelte';
	import Inbox from '@lucide/svelte/icons/inbox';
	import CircleAlert from '@lucide/svelte/icons/circle-alert';
	import type { Item, Page } from '$lib/api';
	import ItemCard from './ItemCard.svelte';
	import Skeleton from './Skeleton.svelte';
	import EmptyState from './EmptyState.svelte';

	/** An endless list of items, fetched a page at a time as the end scrolls into view. A
	 * new query is a new feed: callers remount it (a {#key} block) rather than reset it. */
	interface Props {
		load: (cursor: string | null) => Promise<Page>;
		errorTitle: string;
		emptyDetail: string;
	}

	let { load, errorTitle, emptyDetail }: Props = $props();

	let items: Item[] = $state([]);
	let cursor: string | null = $state(null);
	let exhausted = $state(false);
	let loading = $state(false);
	let error = $state<string | null>(null);

	async function loadMore() {
		if (loading || exhausted) return;
		loading = true;
		try {
			const page = await load(cursor);
			items.push(...page.items);
			cursor = page.next_cursor;
			exhausted = page.next_cursor == null;
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			loading = false;
		}
	}

	onMount(loadMore);

	function sentinel(node: HTMLElement) {
		const observer = new IntersectionObserver(
			(entries) => {
				if (entries[0].isIntersecting) loadMore();
			},
			{ rootMargin: '800px' }
		);
		observer.observe(node);
		return { destroy: () => observer.disconnect() };
	}
</script>

{#if error}
	<EmptyState title={errorTitle} detail={error} error>
		{#snippet icon()}<CircleAlert size={28} />{/snippet}
	</EmptyState>
{:else if items.length === 0 && !exhausted}
	<div class="flex flex-col gap-2" aria-busy="true">
		{#each Array(6), i (i)}
			<div class="rounded-md bg-surface-container-low px-4 py-3">
				<div class="flex items-center gap-3">
					<Skeleton class="size-7 rounded-full" />
					<div class="flex-1"><Skeleton class="h-3.5 w-40" /></div>
					<Skeleton class="h-3 w-12" />
				</div>
				<Skeleton class="mt-3 h-3.5 w-full" />
				<Skeleton class="mt-2 h-3.5 w-2/3" />
			</div>
		{/each}
	</div>
{:else if items.length === 0}
	<EmptyState title="Nothing here" detail={emptyDetail}>
		{#snippet icon()}<Inbox size={28} />{/snippet}
	</EmptyState>
{:else}
	<div class="flex flex-col gap-2">
		{#each items as item (item.platform + item.item_id)}
			<ItemCard {item} />
		{/each}
	</div>
	{#if !exhausted}
		<div use:sentinel class="flex justify-center py-6">
			<Skeleton class="h-3 w-24" />
		</div>
	{:else}
		<p class="py-8 text-center text-label text-on-surface-variant">
			{items.length.toLocaleString()} items — the end
		</p>
	{/if}
{/if}
