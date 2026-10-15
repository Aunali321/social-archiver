<script lang="ts">
	import { onMount } from 'svelte';
	import { api, type Facets } from '$lib/api';
	import { Filters } from '$lib/filters.svelte';
	import FilterPanel from '$lib/components/FilterPanel.svelte';
	import ItemFeed from '$lib/components/ItemFeed.svelte';

	let platforms: string[] = $state([]);
	let facets: Record<string, Facets> = $state({});
	const filters = new Filters();
	let generation = $state(0);

	onMount(() => {
		api.platforms().then((r) => (platforms = r.platforms));
		api.facets().then((r) => (facets = r));
	});
</script>

<svelte:head><title>Timeline · Archive</title></svelte:head>

<div class="mx-auto max-w-6xl px-4 pt-6 xl:grid xl:grid-cols-[15rem_minmax(0,48rem)] xl:justify-center xl:gap-10">
	<!-- Filters: sticky panel beside the feed on wide screens -->
	<aside class="hidden xl:block">
		<div class="sticky top-6">
			<h1 class="mb-5 text-headline text-on-surface">Timeline</h1>
			<FilterPanel variant="panel" {platforms} {facets} {filters} onchange={() => generation++} />
		</div>
	</aside>

	<div class="min-w-0">
		<div class="xl:hidden">
			<h1 class="mb-4 text-headline text-on-surface">Timeline</h1>
			<div class="mb-4">
				<FilterPanel variant="row" {platforms} {facets} {filters} onchange={() => generation++} />
			</div>
		</div>

		{#key generation}
			<ItemFeed
				load={(cursor) => api.items(filters.query, cursor)}
				errorTitle="Couldn't load the timeline"
				emptyDetail="No archived items match these filters. The archive fills as the workers run."
			/>
		{/key}
	</div>
</div>
