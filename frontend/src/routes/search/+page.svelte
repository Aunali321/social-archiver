<script lang="ts">
	import SearchIcon from '@lucide/svelte/icons/search';
	import CircleAlert from '@lucide/svelte/icons/circle-alert';
	import SearchX from '@lucide/svelte/icons/search-x';
	import Sparkles from '@lucide/svelte/icons/sparkles';
	import { api, type SearchHit } from '$lib/api';
	import { platformLabel } from '$lib/format';
	import TextField from '$lib/components/TextField.svelte';
	import Chip from '$lib/components/Chip.svelte';
	import Button from '$lib/components/Button.svelte';
	import ItemCard from '$lib/components/ItemCard.svelte';
	import Skeleton from '$lib/components/Skeleton.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';

	let platforms: string[] = $state([]);
	let semanticPlatforms: string[] = $state([]);

	let query = $state('');
	let selectedPlatform = $state('');
	let semantic = $state(false);

	let hits: SearchHit[] = $state([]);
	let searched = $state(false);
	let loading = $state(false);
	let error = $state<string | null>(null);
	let offset = $state(0);
	let more = $state(false);
	let generation = 0;

	const PAGE = 30;

	$effect(() => {
		api.platforms().then((r) => {
			platforms = r.platforms;
			semanticPlatforms = r.semantic;
		});
	});

	async function search(append = false) {
		const q = query.trim();
		if (!q) return;
		const mine = ++generation;
		if (!append) {
			hits = [];
			offset = 0;
		}
		loading = true;
		error = null;
		try {
			const result = await api.search(
				q,
				semantic ? 'semantic' : 'text',
				{ platforms: selectedPlatform || undefined },
				PAGE,
				offset
			);
			if (mine !== generation) return;
			hits.push(...result.hits);
			semanticPlatforms = result.semantic_platforms;
			// Semantic search is single-shot; text search pages by offset
			more = result.mode === 'text' && result.hits.length === PAGE;
			searched = true;
		} catch (e) {
			if (mine === generation) error = e instanceof Error ? e.message : String(e);
		} finally {
			if (mine === generation) loading = false;
		}
	}
</script>

<svelte:head><title>Search · Archive</title></svelte:head>

<div class="mx-auto max-w-2xl px-4 pt-6">
	<h1 class="mb-4 text-headline text-on-surface">Search</h1>

	<form
		onsubmit={(event) => {
			event.preventDefault();
			search();
		}}
	>
		<TextField label="Search the archive" bind:value={query} type="search" autofocus>
			{#snippet leading()}<SearchIcon size={20} />{/snippet}
		</TextField>
	</form>

	<div class="mt-3 flex flex-wrap items-center gap-2">
		{#each platforms as platform (platform)}
			<Chip
				selected={selectedPlatform === platform}
				onclick={() => {
					selectedPlatform = selectedPlatform === platform ? '' : platform;
					if (searched) search();
				}}
			>
				{platformLabel(platform)}
			</Chip>
		{/each}
		{#if semanticPlatforms.length}
			<Chip
				selected={semantic}
				onclick={() => {
					semantic = !semantic;
					if (searched) search();
				}}
			>
				<Sparkles size={14} /> Semantic
			</Chip>
		{/if}
	</div>

	<div class="mt-5">
		{#if error}
			<EmptyState title="Search failed" detail={error} error>
				{#snippet icon()}<CircleAlert size={28} />{/snippet}
			</EmptyState>
		{:else if loading && hits.length === 0}
			<div class="flex flex-col gap-3">
				{#each Array(4), i (i)}
					<div class="rounded-md bg-surface-container-low p-4">
						<Skeleton class="h-3.5 w-44" />
						<Skeleton class="mt-3 h-3.5 w-full" />
						<Skeleton class="mt-2 h-3.5 w-1/2" />
					</div>
				{/each}
			</div>
		{:else if searched && hits.length === 0}
			<EmptyState title="No matches" detail="Nothing in the archive matches that query.">
				{#snippet icon()}<SearchX size={28} />{/snippet}
			</EmptyState>
		{:else if hits.length}
			<p class="mb-3 text-label text-on-surface-variant">
				{hits.length}{more ? '+' : ''} results
			</p>
			<div class="flex flex-col gap-3">
				{#each hits as hit (hit.item.platform + hit.item.item_id)}
					<ItemCard item={hit.item} snippet={hit.snippet} />
				{/each}
			</div>
			{#if more}
				<div class="flex justify-center py-6">
					<Button
						variant="tonal"
						disabled={loading}
						onclick={() => {
							offset += PAGE;
							search(true);
						}}
					>
						{loading ? 'Loading…' : 'More results'}
					</Button>
				</div>
			{/if}
		{:else}
			<EmptyState
				title="Search everything you've archived"
				detail="Full-text across posts, captions, chat messages and media descriptions{semanticPlatforms.length
					? '; toggle Semantic for meaning-based search'
					: ''}."
			>
				{#snippet icon()}<SearchIcon size={28} />{/snippet}
			</EmptyState>
		{/if}
	</div>
</div>
