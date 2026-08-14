<script lang="ts">
	import { onMount } from 'svelte';
	import Inbox from '@lucide/svelte/icons/inbox';
	import CircleAlert from '@lucide/svelte/icons/circle-alert';
	import { api, type Facets, type Item, type ItemFilters } from '$lib/api';
	import TimelineFilters from '$lib/components/TimelineFilters.svelte';
	import ItemCard from '$lib/components/ItemCard.svelte';
	import Skeleton from '$lib/components/Skeleton.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';

	let platforms: string[] = $state([]);
	let facets: Record<string, Facets> = $state({});

	let selectedPlatform = $state('');
	let category = $state('');
	let subreddit = $state('');
	let origin = $state('');
	let mediaOnly = $state(false);
	let seedsOnly = $state(false);

	let items: Item[] = $state([]);
	let cursor: string | null = $state(null);
	let exhausted = $state(false);
	let loading = $state(false);
	let error = $state<string | null>(null);
	let generation = 0;

	const filters = $derived<ItemFilters>({
		platforms: selectedPlatform || undefined,
		category: category || undefined,
		subreddit: subreddit || undefined,
		origin: origin || undefined,
		has_media: mediaOnly ? true : undefined,
		seeds_only: seedsOnly ? true : undefined
	});

	const activeFacets = $derived(selectedPlatform ? (facets[selectedPlatform] ?? null) : null);

	async function reload() {
		const mine = ++generation;
		items = [];
		cursor = null;
		exhausted = false;
		error = null;
		await loadMore(mine);
	}

	async function loadMore(mine = generation) {
		if (loading || exhausted) return;
		loading = true;
		try {
			const page = await api.items(filters, cursor);
			if (mine !== generation) return;
			items.push(...page.items);
			cursor = page.next_cursor;
			exhausted = page.next_cursor == null;
		} catch (e) {
			if (mine === generation) error = e instanceof Error ? e.message : String(e);
		} finally {
			if (mine === generation) loading = false;
		}
	}

	function selectPlatform(platform: string) {
		selectedPlatform = selectedPlatform === platform ? '' : platform;
		category = '';
		subreddit = '';
		origin = '';
		reload();
	}

	function toggleMediaOnly() {
		mediaOnly = !mediaOnly;
		reload();
	}

	function toggleSeedsOnly() {
		seedsOnly = !seedsOnly;
		reload();
	}

	onMount(() => {
		api.platforms().then((r) => (platforms = r.platforms));
		api.facets().then((r) => (facets = r));
		reload();
	});

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

<svelte:head><title>Timeline · Archive</title></svelte:head>

<div class="mx-auto max-w-6xl px-4 pt-6 xl:grid xl:grid-cols-[15rem_minmax(0,48rem)] xl:justify-center xl:gap-10">
	<!-- Filters: sticky panel beside the feed on wide screens -->
	<aside class="hidden xl:block">
		<div class="sticky top-6">
			<h1 class="mb-5 text-headline text-on-surface">Timeline</h1>
			<TimelineFilters
				variant="panel"
				{platforms}
				facets={activeFacets}
				{selectedPlatform}
				bind:category
				bind:subreddit
				bind:origin
				{mediaOnly}
				{seedsOnly}
				onPlatform={selectPlatform}
				onMediaOnly={toggleMediaOnly}
				onSeedsOnly={toggleSeedsOnly}
				onFacet={reload}
			/>
		</div>
	</aside>

	<div class="min-w-0">
		<div class="xl:hidden">
			<h1 class="mb-4 text-headline text-on-surface">Timeline</h1>
			<div class="mb-4">
				<TimelineFilters
					variant="row"
					{platforms}
					facets={activeFacets}
					{selectedPlatform}
					bind:category
					bind:subreddit
					bind:origin
					{mediaOnly}
					{seedsOnly}
					onPlatform={selectPlatform}
					onMediaOnly={toggleMediaOnly}
					onSeedsOnly={toggleSeedsOnly}
					onFacet={reload}
				/>
			</div>
		</div>

		{#if error}
			<EmptyState title="Couldn't load the timeline" detail={error} error>
				{#snippet icon()}<CircleAlert size={28} />{/snippet}
			</EmptyState>
		{:else if items.length === 0 && loading}
			<div class="flex flex-col gap-2">
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
			<EmptyState
				title="Nothing here"
				detail="No archived items match these filters. The archive fills as the workers run."
			>
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
					{items.length.toLocaleString()} items — end of the archive
				</p>
			{/if}
		{/if}
	</div>
</div>
