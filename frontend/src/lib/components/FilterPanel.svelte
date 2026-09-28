<script lang="ts">
	import Heart from '@lucide/svelte/icons/heart';
	import Link from '@lucide/svelte/icons/link';
	import SlidersHorizontal from '@lucide/svelte/icons/sliders-horizontal';
	import Chip from './Chip.svelte';
	import Select from './Select.svelte';
	import AuthorField from './AuthorField.svelte';
	import { api, type Chat, type Facets } from '$lib/api';
	import {
		FORMAT_OPTIONS,
		MEDIA_OPTIONS,
		SINCE_OPTIONS,
		kindOptions,
		type Filters
	} from '$lib/filters.svelte';
	import { platformLabel } from '$lib/format';

	/** Filter controls for the timeline and search: a stacked panel for the desktop sidebar,
	 * or a row (mobile) whose refinements fold behind a toggle. Every change calls back for
	 * a reload. */
	interface Props {
		variant: 'row' | 'panel';
		platforms: string[];
		facets: Record<string, Facets>;
		filters: Filters;
		onchange: () => void;
	}

	let { variant, platforms, facets, filters, onchange }: Props = $props();

	let expanded = $state(false);
	let chats: Chat[] = $state([]);

	// Facet values only mean something within one platform
	const active = $derived(filters.platform ? (facets[filters.platform] ?? null) : null);
	const kinds = $derived(kindOptions(filters.platform));

	$effect(() => {
		if (filters.platform === 'whatsapp' && !chats.length) {
			api.chats('whatsapp').then((found) => (chats = found));
		}
	});

	// Alphabetical: a picker is scanned by name, not by recency
	const chatOptions = $derived(
		chats
			.map((chat) => ({
				value: chat.chat_id,
				label: `${chat.name || chat.chat_id} (${chat.message_count.toLocaleString()})`
			}))
			.sort((a, b) => a.label.localeCompare(b.label))
	);

	const options = (record: Record<string, number>) =>
		Object.entries(record).map(([value, count]) => ({
			value,
			label: `${value} (${count.toLocaleString()})`
		}));

	const dateInput =
		'h-8 rounded-sm border border-outline-variant bg-transparent px-2 text-label-lg text-on-surface-variant outline-none focus-visible:border-primary';
</script>

{#snippet refine()}
	<Chip
		selected={filters.seedsOnly}
		onclick={() => {
			filters.seedsOnly = !filters.seedsOnly;
			onchange();
		}}
	>
		<Heart size={14} /> Liked/saved only
	</Chip>
	<Chip
		selected={filters.hasLink}
		onclick={() => {
			filters.hasLink = !filters.hasLink;
			onchange();
		}}
	>
		<Link size={14} /> Has link
	</Chip>
	<Select
		label="Date"
		bind:value={filters.since}
		allLabel="Any time"
		options={SINCE_OPTIONS}
		onchange={() => {
			if (filters.since !== 'custom' || filters.dateFrom || filters.dateTo) onchange();
		}}
	/>
	{#if filters.since === 'custom'}
		<div class={variant === 'panel' ? 'flex w-full flex-col items-start gap-1' : 'flex items-center gap-2'}>
			<input
				type="date"
				aria-label="From date"
				class={dateInput}
				max={filters.dateTo || undefined}
				bind:value={filters.dateFrom}
				{onchange}
			/>
			<span class="text-label text-on-surface-variant">to</span>
			<input
				type="date"
				aria-label="To date"
				class={dateInput}
				min={filters.dateFrom || undefined}
				bind:value={filters.dateTo}
				{onchange}
			/>
		</div>
	{/if}
	<Select
		label="Media"
		bind:value={filters.media}
		allLabel="With or without media"
		options={MEDIA_OPTIONS}
		{onchange}
	/>
	{#if kinds.length}
		<Select label="Type" bind:value={filters.kind} allLabel="All types" options={kinds} {onchange} />
	{/if}
	<AuthorField bind:value={filters.author} platform={filters.platform} {onchange} />
	{#if filters.platform === 'instagram'}
		<Select
			label="Format"
			bind:value={filters.postFormat}
			allLabel="All formats"
			options={FORMAT_OPTIONS}
			{onchange}
		/>
	{/if}
	{#if filters.platform === 'whatsapp' && chats.length}
		<Select
			label="Chat"
			bind:value={filters.chat}
			allLabel="All chats"
			options={chatOptions}
			{onchange}
		/>
	{/if}
	{#if active}
		<Select
			label="Category"
			bind:value={filters.category}
			allLabel="All categories"
			options={options(active.categories)}
			{onchange}
		/>
		{#if Object.keys(active.subreddits).length}
			<Select
				label="Subreddit"
				bind:value={filters.subreddit}
				allLabel="All subreddits"
				options={options(active.subreddits)}
				{onchange}
			/>
		{/if}
		{#if Object.keys(active.origins).length > 1}
			<Select
				label="Origin"
				bind:value={filters.origin}
				allLabel="All origins"
				options={options(active.origins)}
				{onchange}
			/>
		{/if}
	{/if}
{/snippet}

{#snippet platformChips()}
	{#each platforms as platform (platform)}
		<Chip
			selected={filters.platform === platform}
			onclick={() => {
				filters.selectPlatform(platform);
				onchange();
			}}
		>
			{platformLabel(platform)}
		</Chip>
	{/each}
{/snippet}

{#if variant === 'panel'}
	<div class="flex flex-col gap-4">
		<section>
			<h2 class="mb-2 text-label font-medium tracking-wide text-on-surface-variant uppercase">
				Platform
			</h2>
			<div class="flex flex-wrap gap-2">{@render platformChips()}</div>
		</section>
		<section>
			<h2 class="mb-2 text-label font-medium tracking-wide text-on-surface-variant uppercase">
				Refine
			</h2>
			<div class="flex flex-col items-start gap-2">{@render refine()}</div>
		</section>
	</div>
{:else}
	<div class="flex flex-wrap items-center gap-2">
		{@render platformChips()}
		<Chip selected={expanded} onclick={() => (expanded = !expanded)}>
			<SlidersHorizontal size={14} /> Filters{filters.active ? ` · ${filters.active}` : ''}
		</Chip>
	</div>
	{#if expanded}
		<div class="mt-3 flex flex-wrap items-center gap-2">{@render refine()}</div>
	{/if}
{/if}
