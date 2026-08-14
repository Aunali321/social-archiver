<script lang="ts">
	import ImageIcon from '@lucide/svelte/icons/image';
	import Heart from '@lucide/svelte/icons/heart';
	import Chip from './Chip.svelte';
	import Select from './Select.svelte';
	import type { Facets } from '$lib/api';
	import { platformLabel } from '$lib/format';

	/** The timeline's filter controls, laid out as a wrap row (mobile) or a stacked panel
	 * (desktop sidebar). State lives in the page; every change calls back for a reload. */
	interface Props {
		variant: 'row' | 'panel';
		platforms: string[];
		facets: Facets | null; // facets of the selected platform, null when browsing all
		selectedPlatform: string;
		category: string;
		subreddit: string;
		origin: string;
		mediaOnly: boolean;
		seedsOnly: boolean;
		onPlatform: (platform: string) => void;
		onMediaOnly: () => void;
		onSeedsOnly: () => void;
		onFacet: () => void;
	}

	let {
		variant,
		platforms,
		facets,
		selectedPlatform,
		category = $bindable(),
		subreddit = $bindable(),
		origin = $bindable(),
		mediaOnly,
		seedsOnly,
		onPlatform,
		onMediaOnly,
		onSeedsOnly,
		onFacet
	}: Props = $props();

	const options = (record: Record<string, number>) =>
		Object.entries(record).map(([value, count]) => ({
			value,
			label: `${value} (${count.toLocaleString()})`
		}));
</script>

<div class={variant === 'panel' ? 'flex flex-col gap-4' : 'flex flex-wrap items-center gap-2'}>
	<section class={variant === 'panel' ? '' : 'contents'}>
		{#if variant === 'panel'}
			<h2 class="mb-2 text-label font-medium tracking-wide text-on-surface-variant uppercase">
				Platform
			</h2>
		{/if}
		<div class="flex flex-wrap gap-2">
			{#each platforms as platform (platform)}
				<Chip selected={selectedPlatform === platform} onclick={() => onPlatform(platform)}>
					{platformLabel(platform)}
				</Chip>
			{/each}
		</div>
	</section>

	<section class={variant === 'panel' ? '' : 'contents'}>
		{#if variant === 'panel'}
			<h2 class="mb-2 text-label font-medium tracking-wide text-on-surface-variant uppercase">
				Refine
			</h2>
		{/if}
		<div class={variant === 'panel' ? 'flex flex-col items-start gap-2' : 'contents'}>
			<Chip selected={mediaOnly} onclick={onMediaOnly}><ImageIcon size={14} /> Media</Chip>
			<Chip selected={seedsOnly} onclick={onSeedsOnly}><Heart size={14} /> Liked/saved only</Chip>
			{#if facets}
				<Select
					label="Category"
					bind:value={category}
					allLabel="All categories"
					options={options(facets.categories)}
					onchange={onFacet}
				/>
				{#if Object.keys(facets.subreddits).length}
					<Select
						label="Subreddit"
						bind:value={subreddit}
						allLabel="All subreddits"
						options={options(facets.subreddits)}
						onchange={onFacet}
					/>
				{/if}
				{#if Object.keys(facets.origins).length > 1}
					<Select
						label="Origin"
						bind:value={origin}
						allLabel="All origins"
						options={options(facets.origins)}
						onchange={onFacet}
					/>
				{/if}
			{/if}
		</div>
	</section>
</div>
