<script lang="ts">
	import CircleAlert from '@lucide/svelte/icons/circle-alert';
	import ChartColumn from '@lucide/svelte/icons/chart-column';
	import { api, type ArchiveStats } from '$lib/api';
	import { platformLabel } from '$lib/format';
	import PlatformBadge from '$lib/components/PlatformBadge.svelte';
	import BarChart from '$lib/components/BarChart.svelte';
	import Skeleton from '$lib/components/Skeleton.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';

	let stats = $state<ArchiveStats[] | null>(null);
	let error = $state<string | null>(null);

	$effect(() => {
		api
			.stats()
			.then((result) => (stats = result))
			.catch((e) => (error = e instanceof Error ? e.message : String(e)));
	});

	const grandTotal = $derived((stats ?? []).reduce((sum, s) => sum + s.total, 0));

	function span(s: ArchiveStats): string {
		if (!s.oldest || !s.newest) return '';
		const year = (iso: string) => iso.slice(0, 4);
		return year(s.oldest) === year(s.newest)
			? year(s.oldest)
			: `${year(s.oldest)} – ${year(s.newest)}`;
	}

	/** Pipeline progress per stage, as done/total with failed counted out loud */
	function stage(record: Record<string, number>, doneKeys: string[]): { done: number; failed: number } {
		const done = doneKeys.reduce((sum, key) => sum + (record[key] ?? 0), 0);
		return { done, failed: record.failed ?? 0 };
	}
</script>

<svelte:head><title>Stats · Archive</title></svelte:head>

<div class="mx-auto max-w-4xl px-4 pt-6">
	<h1 class="mb-1 text-headline text-on-surface">Stats</h1>
	{#if stats}
		<p class="mb-5 text-body text-on-surface-variant">
			{grandTotal.toLocaleString()} items across {stats.length} platform{stats.length === 1 ? '' : 's'}
		</p>
	{/if}

	{#if error}
		<EmptyState title="Couldn't load stats" detail={error} error>
			{#snippet icon()}<CircleAlert size={28} />{/snippet}
		</EmptyState>
	{:else if stats == null}
		<div class="grid gap-4">
			{#each Array(3), i (i)}
				<div class="rounded-md bg-surface-container-low p-5">
					<Skeleton class="h-5 w-40" />
					<Skeleton class="mt-4 h-28 w-full" />
				</div>
			{/each}
		</div>
	{:else if stats.length === 0}
		<EmptyState title="Nothing archived yet" detail="Stats appear once a platform has items.">
			{#snippet icon()}<ChartColumn size={28} />{/snippet}
		</EmptyState>
	{:else}
		<div class="grid gap-4">
			{#each stats as s (s.platform)}
				{@const archive = stage(s.archive, ['archived'])}
				{@const upload = stage(s.upload, ['done', 'skipped'])}
				{@const embed = stage(s.embed, ['done', 'skipped'])}
				<section class="rounded-md bg-surface-container-low p-5">
					<header class="flex items-center gap-3">
						<PlatformBadge platform={s.platform} size={18} />
						<h2 class="flex-1 text-title-lg text-on-surface">{platformLabel(s.platform)}</h2>
						<span class="text-label text-on-surface-variant">{span(s)}</span>
					</header>

					<!-- Stat tiles -->
					<dl class="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
						{#each [['Items', s.total], ['Authors', s.authors], ['With media', s.with_media], ['Media on disk', s.with_local_media]] as const as [label, value] (label)}
							<div class="rounded-sm bg-surface-container px-3 py-2.5">
								<dt class="text-label text-on-surface-variant">{label}</dt>
								<dd class="text-title-lg text-on-surface tabular-nums">{value.toLocaleString()}</dd>
							</div>
						{/each}
					</dl>

					<!-- Items per month -->
					<div class="mt-5">
						<BarChart
							data={s.by_month}
							color="var(--chart-{s.platform}, var(--m3-primary))"
							label={platformLabel(s.platform)}
						/>
					</div>

					<!-- Categories -->
					<div class="mt-4 flex flex-wrap gap-1.5">
						{#each Object.entries(s.categories).sort((a, b) => b[1] - a[1]) as [category, count] (category)}
							<span class="rounded-full bg-surface-container-high px-3 py-1 text-label text-on-surface-variant">
								{category} · {count.toLocaleString()}
							</span>
						{/each}
					</div>

					<!-- Pipeline -->
					<dl class="mt-4 grid grid-cols-3 gap-3 border-t border-outline-variant pt-4 text-label">
						{#each [['Archived', archive], ['Uploaded', upload], ['Embedded', embed]] as const as [label, st] (label)}
							<div>
								<dt class="text-on-surface-variant">{label}</dt>
								<dd class="text-body-lg text-on-surface tabular-nums">
									{st.done.toLocaleString()}
									<span class="text-label text-on-surface-variant">
										/ {s.total.toLocaleString()}</span
									>
								</dd>
								{#if st.failed > 0}
									<dd class="mt-0.5 text-[var(--m3-error)]">{st.failed.toLocaleString()} failed</dd>
								{/if}
							</div>
						{/each}
					</dl>
				</section>
			{/each}
		</div>
	{/if}
	<div class="h-8"></div>
</div>
