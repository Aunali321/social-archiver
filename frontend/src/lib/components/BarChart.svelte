<script lang="ts">
	/**
	 * Monthly-count bar chart, single series. Thin rounded bars anchored to the baseline,
	 * 2px gaps, recessive axis, hover tooltip, direct label on the peak only.
	 */
	interface Props {
		data: Record<string, number>; // "YYYY-MM" -> count, assumed sorted keys
		color: string; // CSS color for the series
		label: string; // series name for a11y
	}

	let { data, color, label }: Props = $props();

	const entries = $derived(Object.entries(data));
	const max = $derived(Math.max(1, ...entries.map(([, count]) => count)));
	const peak = $derived(entries.findIndex(([, count]) => count === max));

	const W = 560;
	const H = 120;
	const BASE = H - 16;

	const barWidth = $derived(Math.max(1, Math.min(14, W / Math.max(1, entries.length) - 2)));
	const step = $derived(W / Math.max(1, entries.length));

	let hover = $state<number | null>(null);

	function labelFor(month: string): string {
		const [year, m] = month.split('-');
		return new Date(Number(year), Number(m) - 1).toLocaleDateString(undefined, {
			month: 'short',
			year: 'numeric'
		});
	}

	// Year ticks: first month of each year present, thinned to at most 6
	const ticks = $derived.by(() => {
		const firsts = entries
			.map(([month], index) => ({ month, index }))
			.filter(({ month }, i, all) => i === 0 || month.slice(0, 4) !== all[i - 1].month.slice(0, 4));
		const every = Math.ceil(firsts.length / 6);
		return firsts.filter((_, i) => i % every === 0);
	});
</script>

{#if entries.length === 0}
	<p class="py-6 text-center text-label text-on-surface-variant">No dated items</p>
{:else}
	<div class="relative">
		<svg
			viewBox="0 0 {W} {H}"
			class="w-full"
			role="img"
			aria-label="{label}: items per month"
			onmouseleave={() => (hover = null)}
		>
			<line x1="0" y1={BASE} x2={W} y2={BASE} stroke="var(--m3-outline-variant)" stroke-width="1" />
			{#each entries as [month, count], index (month)}
				{@const height = Math.max(count > 0 ? 2 : 0, (count / max) * (BASE - 18))}
				<!-- generous hit target; the visible bar is thinner -->
				<rect
					x={index * step}
					y="0"
					width={step}
					height={BASE}
					fill="transparent"
					onmouseenter={() => (hover = index)}
					role="presentation"
				/>
				{#if height > 0}
					<rect
						x={index * step + (step - barWidth) / 2}
						y={BASE - height}
						width={barWidth}
						height={height}
						rx={Math.min(4, barWidth / 2)}
						fill={color}
						opacity={hover === null || hover === index ? 1 : 0.45}
						style="clip-path: inset(0 0 {Math.min(4, barWidth / 2)}px 0 round {Math.min(
							4,
							barWidth / 2
						)}px {Math.min(4, barWidth / 2)}px 0 0)"
						pointer-events="none"
					/>
				{/if}
				{#if index === peak && hover === null}
					{@const center = index * step + step / 2}
					<text
						x={center > W - 30 ? W - 2 : Math.max(24, center)}
						y={BASE - height - 5}
						text-anchor={center > W - 30 ? 'end' : 'middle'}
						class="fill-[var(--m3-on-surface-variant)] text-[10px]"
					>
						{count.toLocaleString()}
					</text>
				{/if}
			{/each}
			{#each ticks as tick (tick.month)}
				<text
					x={tick.index * step + 2}
					y={H - 4}
					class="fill-[var(--m3-on-surface-variant)] text-[10px]"
				>
					{tick.month.slice(0, 4)}
				</text>
			{/each}
		</svg>
		{#if hover !== null && entries[hover]}
			<div
				class="pointer-events-none absolute -top-1 rounded-sm bg-inverse-surface px-2 py-1 text-label whitespace-nowrap text-inverse-on-surface shadow-e2"
				style="left: {Math.min(86, Math.max(0, (hover / entries.length) * 100))}%"
			>
				{labelFor(entries[hover][0])} · {entries[hover][1].toLocaleString()}
			</div>
		{/if}
	</div>
{/if}
