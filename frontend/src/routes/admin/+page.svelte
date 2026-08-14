<script lang="ts">
	import CircleAlert from '@lucide/svelte/icons/circle-alert';
	import Play from '@lucide/svelte/icons/play';
	import QrCode from '@lucide/svelte/icons/qr-code';
	import { api, type ControlStatus, type SourcesResult } from '$lib/api';
	import { platformLabel } from '$lib/format';
	import PlatformBadge from '$lib/components/PlatformBadge.svelte';
	import Button from '$lib/components/Button.svelte';
	import Chip from '$lib/components/Chip.svelte';
	import Select from '$lib/components/Select.svelte';
	import TextField from '$lib/components/TextField.svelte';
	import Dialog from '$lib/components/Dialog.svelte';
	import Skeleton from '$lib/components/Skeleton.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import { toast } from '$lib/components/snackbar.svelte';

	let status = $state<ControlStatus | null>(null);
	let sources = $state<SourcesResult | null>(null);
	let error = $state<string | null>(null);

	let sourcePlatform = $state('');
	let sourceTarget = $state('');

	const INTERVALS = [30, 60, 120, 180, 240, 360, 480, 720, 1440];

	async function refresh() {
		try {
			[status, sources] = await Promise.all([api.status(), api.sources()]);
			error = null;
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		}
	}

	$effect(() => {
		refresh();
		const timer = setInterval(refresh, 5000);
		return () => clearInterval(timer);
	});

	async function act(action: () => Promise<{ started: string }>) {
		try {
			const result = await action();
			toast(result.started);
		} catch (e) {
			toast(e instanceof Error ? e.message : String(e), { error: true });
		}
		refresh();
	}

	function toggleCategory(platform: string, category: string) {
		const entry = status?.platforms.find((p) => p.platform === platform);
		if (!entry) return;
		const categories = entry.scheduled_categories.includes(category)
			? entry.scheduled_categories.filter((c) => c !== category)
			: [...entry.scheduled_categories, category];
		act(() =>
			api.schedule({
				platform,
				enabled: entry.scheduled,
				categories,
				interval_minutes: entry.interval_minutes ?? 240
			})
		);
	}

	function intervalOptions(current: number) {
		const minutes = INTERVALS.includes(current) ? INTERVALS : [...INTERVALS, current].sort((a, b) => a - b);
		return minutes.map((m) => ({
			value: String(m),
			label: `every ${m % 60 ? `${m}m` : `${m / 60}h`}`
		}));
	}

	/* WhatsApp pairing */
	let pairOpen = $state(false);
	let pairQr = $state<string | null>(null);
	let pairMessage = $state('starting…');
	let pairTimer: ReturnType<typeof setTimeout> | undefined;

	async function pair(platform: string) {
		pairOpen = true;
		pairQr = null;
		pairMessage = 'requesting a QR from WhatsApp…';
		try {
			const started = await api.pairStart(platform);
			if (started.error) {
				pairMessage = started.error;
				return;
			}
			pollPair(platform);
		} catch (e) {
			pairMessage = e instanceof Error ? e.message : String(e);
		}
	}

	async function pollPair(platform: string) {
		const state = await api.pairState(platform).catch(() => null);
		if (!pairOpen) return;
		if (state?.paired) {
			pairMessage = 'Paired. The bridge starts syncing on its own.';
			pairQr = null;
			return;
		}
		if (state?.qr) {
			pairQr = state.qr;
			pairMessage = 'WhatsApp → Linked devices → Link a device';
		} else if (!state?.pairing) {
			pairMessage = 'Pairing stopped; close and retry.';
			return;
		}
		pairTimer = setTimeout(() => pollPair(platform), 2000);
	}

	function closePair() {
		pairOpen = false;
		clearTimeout(pairTimer);
		refresh();
	}

	const statusTone: Record<string, string> = {
		done: 'text-[var(--m3-success)]',
		running: 'text-primary',
		queued: 'text-[var(--m3-warning)]',
		failed: 'text-error',
		interrupted: 'text-error'
	};
</script>

<svelte:head><title>Admin · Archive</title></svelte:head>

<div class="mx-auto max-w-4xl px-4 pt-6">
	<h1 class="mb-4 text-headline text-on-surface">Admin</h1>

	{#if error && !status}
		<EmptyState title="Couldn't reach the archiver" detail={error} error>
			{#snippet icon()}<CircleAlert size={28} />{/snippet}
		</EmptyState>
	{:else if !status}
		<div class="grid gap-4 lg:grid-cols-2">
			{#each Array(4), i (i)}
				<div class="rounded-md bg-surface-container-low p-5">
					<Skeleton class="h-5 w-32" />
					<Skeleton class="mt-4 h-24 w-full" />
				</div>
			{/each}
		</div>
	{:else}
		<!-- Platforms -->
		<div class="grid items-start gap-4 lg:grid-cols-2">
			{#each status.platforms as p (p.platform)}
				<section class="rounded-md bg-surface-container-low p-5">
					<header class="flex items-center gap-3">
						<PlatformBadge platform={p.platform} />
						<h2 class="flex-1 text-title-lg text-on-surface">{platformLabel(p.platform)}</h2>
						<span
							class="rounded-full border px-3 py-1 text-label {p.scheduled
								? 'border-[var(--m3-success)] text-[var(--m3-success)]'
								: 'border-[var(--m3-warning)] text-[var(--m3-warning)]'}"
						>
							{p.scheduled ? `next ${p.next_run}` : 'paused'}
						</span>
					</header>

					{#if p.error}
						<p class="mt-3 text-body text-on-surface-variant italic">{p.error}</p>
					{:else}
						<dl class="mt-3 grid grid-cols-3 gap-2 text-label">
							<div>
								<dt class="text-on-surface-variant">items</dt>
								<dd class="text-body-lg text-on-surface tabular-nums">{p.total.toLocaleString()}</dd>
							</div>
							{#each [['archive', p.archive], ['upload', p.upload]] as const as [label, record] (label)}
								<div>
									<dt class="text-on-surface-variant">{label}</dt>
									<dd class="text-body text-on-surface">
										{#each Object.entries(record) as [key, count], i (key)}
											{i > 0 ? ' · ' : ''}<span class={statusTone[key] ?? ''}>{count.toLocaleString()} {key}</span>
										{/each}
									</dd>
								</div>
							{/each}
						</dl>
					{/if}

					{#if p.sidecar}
						<div class="mt-3 flex items-center gap-2 text-label text-on-surface-variant">
							bridge: {p.sidecar}
							{#if p.sidecar === 'not paired'}
								<Button variant="tonal" onclick={() => pair(p.platform)}>
									<QrCode size={16} /> Pair
								</Button>
							{/if}
						</div>
					{/if}

					<!-- Schedule -->
					<div class="mt-4 border-t border-outline-variant pt-3">
						<p class="mb-2 text-label text-on-surface-variant">Scheduled categories</p>
						<div class="flex flex-wrap items-center gap-2">
							{#each p.categories as category (category)}
								<Chip
									selected={p.scheduled_categories.includes(category)}
									onclick={() => toggleCategory(p.platform, category)}
								>
									{category}
								</Chip>
							{/each}
							<select
								aria-label="{p.platform} cycle interval"
								value={String(p.interval_minutes ?? 240)}
								onchange={(event) =>
									act(() =>
										api.schedule({
											platform: p.platform,
											enabled: p.scheduled,
											categories: p.scheduled_categories,
											interval_minutes: Number(event.currentTarget.value)
										})
									)}
								class="h-8 cursor-pointer rounded-sm border border-outline-variant bg-transparent px-2 text-label-lg text-on-surface-variant outline-none focus-visible:border-primary"
							>
								{#each intervalOptions(p.interval_minutes ?? 240) as option (option.value)}
									<option value={option.value}>{option.label}</option>
								{/each}
							</select>
							<Button
								variant="outlined"
								onclick={() =>
									act(() =>
										api.schedule({
											platform: p.platform,
											enabled: !p.scheduled,
											categories: p.scheduled_categories,
											interval_minutes: p.interval_minutes ?? 240
										})
									)}
							>
								{p.scheduled ? 'Pause' : 'Resume'}
							</Button>
						</div>
					</div>

					<!-- Run now -->
					<div class="mt-3 flex flex-wrap gap-2 border-t border-outline-variant pt-3">
						{#each ['archive', 'upload', 'embed'] as job (job)}
							<Button variant="text" onclick={() => act(() => api.run({ platform: p.platform, job }))}>
								{job}
							</Button>
						{/each}
						<Button variant="tonal" onclick={() => act(() => api.cycle(p.platform))}>
							<Play size={16} /> Cycle
						</Button>
					</div>
				</section>
			{/each}
		</div>

		<!-- Sources -->
		<section class="mt-6 rounded-md bg-surface-container-low p-5">
			<h2 class="text-title-lg text-on-surface">Sources</h2>
			<p class="mt-1 text-body text-on-surface-variant">
				Accounts and subreddits archived in their own right; tracked ones re-sync on their
				platform's cycle.
			</p>
			{#if sources}
				<div class="mt-3 overflow-x-auto">
					<table class="w-full text-body">
						<thead>
							<tr class="border-b border-outline-variant text-left text-label text-on-surface-variant">
								<th class="py-2 pr-3 font-medium">source</th>
								<th class="py-2 pr-3 font-medium">state</th>
								<th class="py-2 pr-3 font-medium">items</th>
								<th class="py-2 pr-3 font-medium">last run</th>
								<th class="py-2 font-medium"><span class="sr-only">actions</span></th>
							</tr>
						</thead>
						<tbody>
							{#each sources.sources as source (source.platform + source.kind + source.target)}
								<tr class="border-b border-outline-variant">
									<td class="py-2 pr-3 text-on-surface">
										{source.platform} · {source.kind}:{source.target}
									</td>
									<td class="py-2 pr-3 {source.enabled ? 'text-[var(--m3-success)]' : 'text-[var(--m3-warning)]'}">
										{source.enabled ? 'syncing' : 'paused'}
									</td>
									<td class="py-2 pr-3 tabular-nums">{source.items.toLocaleString()}</td>
									<td class="py-2 pr-3 text-on-surface-variant">{source.last_run ?? 'never'}</td>
									<td class="py-2">
										<div class="flex gap-1">
											{#each [['sync', () => api.sourceAction('run', source, '?full=false')], ['full', () => api.sourceAction('run', source, '?full=true')], [source.enabled ? 'pause' : 'resume', () => api.sourceAction('enabled', source, `?enabled=${!source.enabled}`)], ['untrack', () => api.sourceAction('remove', source)]] as const as [label, action] (label)}
												<Button variant="text" onclick={() => act(action)}>{label}</Button>
											{/each}
										</div>
									</td>
								</tr>
							{:else}
								<tr><td colspan="5" class="py-4 text-on-surface-variant italic">Nothing tracked</td></tr>
							{/each}
						</tbody>
					</table>
				</div>
				{#if Object.values(sources.kinds).some((kinds) => kinds.length)}
					<form
						class="mt-4 flex flex-wrap items-center gap-2"
						onsubmit={(event) => {
							event.preventDefault();
							if (!sourceTarget.trim()) return;
							const platform = sourcePlatform || Object.keys(sources!.kinds).find((p) => sources!.kinds[p].length)!;
							act(() => api.sourceAction('add', { platform, target: sourceTarget.trim() }));
							sourceTarget = '';
						}}
					>
						<Select
							label="Source platform"
							bind:value={sourcePlatform}
							options={Object.entries(sources.kinds)
								.filter(([, kinds]) => kinds.length)
								.map(([platform]) => ({ value: platform, label: platformLabel(platform) }))}
						/>
						<TextField label="account or subreddit" bind:value={sourceTarget} class="!h-10 flex-1" />
						<Button variant="tonal" type="submit">Track</Button>
					</form>
				{/if}
			{/if}
		</section>

		<!-- Queue -->
		<section class="mt-6 rounded-md bg-surface-container-low p-5">
			<h2 class="text-title-lg text-on-surface">Queue</h2>
			<div class="mt-3 overflow-x-auto">
				<table class="w-full text-body">
					<thead>
						<tr class="border-b border-outline-variant text-left text-label text-on-surface-variant">
							<th class="py-2 pr-3 font-medium">job</th>
							<th class="py-2 pr-3 font-medium">status</th>
							<th class="py-2 pr-3 font-medium">source</th>
							<th class="py-2 pr-3 font-medium">queued</th>
							<th class="py-2 font-medium"><span class="sr-only">actions</span></th>
						</tr>
					</thead>
					<tbody>
						{#each status.active as job (job.id)}
							<tr class="border-b border-outline-variant">
								<td class="py-2 pr-3 text-on-surface">{job.platform} {job.job} {job.flags}</td>
								<td class="py-2 pr-3 {statusTone[job.status] ?? ''}">{job.status}</td>
								<td class="py-2 pr-3 text-on-surface-variant">{job.source}</td>
								<td class="py-2 pr-3 text-on-surface-variant">{job.queued_at ?? ''}</td>
								<td class="py-2">
									{#if job.status === 'queued'}
										<Button variant="text" onclick={() => act(() => api.cancel(job.id))}>cancel</Button>
									{/if}
								</td>
							</tr>
						{:else}
							<tr><td colspan="5" class="py-4 text-on-surface-variant italic">Nothing queued</td></tr>
						{/each}
					</tbody>
				</table>
			</div>
		</section>

		<!-- History -->
		<section class="mt-6 rounded-md bg-surface-container-low p-5">
			<h2 class="text-title-lg text-on-surface">History</h2>
			<div class="mt-3 overflow-x-auto">
				<table class="w-full text-body">
					<thead>
						<tr class="border-b border-outline-variant text-left text-label text-on-surface-variant">
							<th class="py-2 pr-3 font-medium">job</th>
							<th class="py-2 pr-3 font-medium">status</th>
							<th class="py-2 pr-3 font-medium">finished</th>
							<th class="py-2 font-medium">error</th>
						</tr>
					</thead>
					<tbody>
						{#each status.recent as job (job.id)}
							<tr class="border-b border-outline-variant">
								<td class="py-2 pr-3 text-on-surface">{job.platform} {job.job} {job.flags}</td>
								<td class="py-2 pr-3 {statusTone[job.status] ?? ''}">{job.status}</td>
								<td class="py-2 pr-3 text-on-surface-variant">{job.finished_at ?? ''}</td>
								<td class="max-w-xs truncate py-2 font-mono text-label text-error" title={job.error ?? ''}>
									{job.error?.split('\n').filter(Boolean).at(-1)?.slice(0, 90) ?? ''}
								</td>
							</tr>
						{:else}
							<tr><td colspan="4" class="py-4 text-on-surface-variant italic">Nothing yet</td></tr>
						{/each}
					</tbody>
				</table>
			</div>
		</section>
	{/if}
	<div class="h-8"></div>
</div>

<Dialog open={pairOpen} title="Pair WhatsApp" onclose={closePair}>
	{#if pairQr}
		<!-- The QR stays black-on-white in both themes; an inverted code scans unreliably -->
		<pre class="mx-auto max-w-full overflow-auto rounded-sm bg-white p-2 font-mono text-[7px] leading-[7px] text-black">{pairQr}</pre>
	{/if}
	<p class="mt-2">{pairMessage}</p>
	{#snippet actions()}
		<Button variant="text" onclick={closePair}>Close</Button>
	{/snippet}
</Dialog>
