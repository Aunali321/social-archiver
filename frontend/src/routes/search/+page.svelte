<script lang="ts">
	import { onMount } from 'svelte';
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import SearchIcon from '@lucide/svelte/icons/search';
	import CircleAlert from '@lucide/svelte/icons/circle-alert';
	import SearchX from '@lucide/svelte/icons/search-x';
	import Sparkles from '@lucide/svelte/icons/sparkles';
	import { api, type Facets, type MatchField, type SearchHit, type SearchSort } from '$lib/api';
	import { Filters } from '$lib/filters.svelte';
	import TextField from '$lib/components/TextField.svelte';
	import Chip from '$lib/components/Chip.svelte';
	import Select from '$lib/components/Select.svelte';
	import Button from '$lib/components/Button.svelte';
	import FilterPanel from '$lib/components/FilterPanel.svelte';
	import ItemCard from '$lib/components/ItemCard.svelte';
	import Skeleton from '$lib/components/Skeleton.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';

	const SORTS: { value: SearchSort; label: string }[] = [
		{ value: 'relevance', label: 'Most relevant' },
		{ value: 'newest', label: 'Newest first' },
		{ value: 'oldest', label: 'Oldest first' }
	];

	const MATCHES: { value: MatchField; label: string }[] = [
		{ value: 'text', label: 'In post text' },
		{ value: 'media', label: 'In media descriptions' },
		{ value: 'names', label: 'In names' }
	];

	// What the search box understands beyond plain words
	const TIPS = [
		['"exact phrase"', 'words in this order'],
		['pyth*', 'words starting with pyth'],
		['rust OR go', 'either word'],
		['-java', 'leave out a word'],
		['NEAR(open source, 3)', 'words close together'],
		['from:name', 'by an author'],
		['in:"chat name"', 'in a WhatsApp chat'],
		['r:LocalLLaMA', 'in a subreddit'],
		['platform:reddit', 'one platform'],
		['after:2025-01-01 before:2025-02-01', 'a date range'],
		['has:video has:link -has:media', 'media of a kind (image, video, gif, audio, sticker, document), or a link'],
		['is:reply -is:retweet', 'post, reply, comment, retweet or quote'],
		['is:reel is:dm is:liked', 'reel, carousel, group chat, DM, or liked by you'],
		['likes:>100 views:>10000', 'at least this many'],
		['match:media cat', 'text, media or names only']
	];

	const initial = page.url.searchParams;

	let platforms: string[] = $state([]);
	let semanticPlatforms: string[] = $state([]);
	let facets: Record<string, Facets> = $state({});

	let query = $state(initial.get('q') ?? '');
	let semantic = $state(initial.has('semantic'));
	let sort = $state<SearchSort>((initial.get('sort') as SearchSort | null) ?? 'relevance');
	let match = $state<MatchField | ''>((initial.get('match') as MatchField | null) ?? '');
	const filters = new Filters(initial);

	let hits: SearchHit[] = $state([]);
	let searched = $state(false);
	let loading = $state(false);
	let error = $state<string | null>(null);
	let offset = $state(0);
	let more = $state(false);
	let generation = 0;

	const PAGE = 30;

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
				sort,
				match || undefined,
				filters.query,
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

	/** The URL holds the search, so returning from an item lands on the same results. */
	function submit() {
		const q = query.trim();
		if (!q) return;
		const params = new URLSearchParams({ q });
		if (semantic) params.set('semantic', '1');
		if (sort !== 'relevance') params.set('sort', sort);
		if (match) params.set('match', match);
		filters.writeTo(params);
		goto(`?${params}`, { replaceState: true, keepFocus: true, noScroll: true });
		search();
	}

	function refine() {
		if (searched) submit();
	}

	onMount(() => {
		api.platforms().then((r) => {
			platforms = r.platforms;
			semanticPlatforms = r.semantic;
		});
		api.facets().then((r) => (facets = r));
		search();
	});
</script>

<svelte:head><title>Search · Archive</title></svelte:head>

<div
	class="mx-auto max-w-6xl px-4 pt-6 xl:grid xl:grid-cols-[15rem_minmax(0,48rem)] xl:justify-center xl:gap-10"
>
	<aside class="hidden xl:block">
		<div class="sticky top-6">
			<h1 class="mb-5 text-headline text-on-surface">Search</h1>
			<FilterPanel variant="panel" {platforms} {facets} {filters} onchange={refine} />
		</div>
	</aside>

	<div class="min-w-0">
		<h1 class="mb-4 text-headline text-on-surface xl:hidden">Search</h1>

		<form
			onsubmit={(event) => {
				event.preventDefault();
				submit();
			}}
		>
			<TextField label="Search the archive" bind:value={query} type="search" autofocus>
				{#snippet leading()}<SearchIcon size={20} />{/snippet}
			</TextField>
		</form>

		<div class="mt-3 xl:hidden">
			<FilterPanel variant="row" {platforms} {facets} {filters} onchange={refine} />
		</div>

		<div class="mt-3 flex flex-wrap items-center gap-2">
			{#if semanticPlatforms.length}
				<Chip
					selected={semantic}
					onclick={() => {
						semantic = !semantic;
						refine();
					}}
				>
					<Sparkles size={14} /> Semantic
				</Chip>
			{/if}
			{#if !semantic}
				<Select label="Sort" bind:value={sort} options={SORTS} onchange={refine} />
				<Select
					label="Match in"
					bind:value={match}
					allLabel="Anywhere"
					options={MATCHES}
					onchange={refine}
				/>
			{/if}
			{#if hits.length}
				<p class="ml-auto text-label text-on-surface-variant">
					{hits.length}{more ? '+' : ''} results
				</p>
			{/if}
		</div>

		<div class="mt-4">
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
				<EmptyState title="No matches" detail="Nothing in the archive matches that query and these filters.">
					{#snippet icon()}<SearchX size={28} />{/snippet}
				</EmptyState>
			{:else if hits.length}
				<div class="flex flex-col gap-3">
					{#each hits as hit (hit.item.platform + hit.item.item_id)}
						<ItemCard item={hit.item} snippet={hit.snippet} mediaSnippet={hit.media_snippet} />
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
					detail={'Posts, captions, chat messages and media descriptions.' +
						(semanticPlatforms.length ? ' Toggle Semantic for meaning-based search.' : '')}
				>
					{#snippet icon()}<SearchIcon size={28} />{/snippet}
				</EmptyState>
				<section aria-labelledby="tips" class="mx-auto mt-2 max-w-xl rounded-md bg-surface-container-low p-4">
					<h2 id="tips" class="mb-3 text-title text-on-surface">Search operators</h2>
					<dl class="grid grid-cols-1 gap-x-4 gap-y-2 text-body sm:grid-cols-[auto_1fr]">
						{#each TIPS as [example, meaning] (example)}
							<dt><code class="text-label-lg text-primary">{example}</code></dt>
							<dd class="mb-1 text-on-surface-variant sm:mb-0">{meaning}</dd>
						{/each}
					</dl>
				</section>
			{/if}
		</div>
	</div>
</div>
