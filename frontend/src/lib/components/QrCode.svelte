<script lang="ts">
	/** A QR code from its module grid (rows of 0/1, 1 dark), drawn as SVG so it fills its box
	 * on any screen. Black on white in both themes: an inverted code scans unreliably. */
	interface Props {
		grid: string;
		label: string;
	}

	let { grid, label }: Props = $props();

	// The spec's quiet zone; scanners find the code by the blank border around it
	const QUIET = 4;

	const rows = $derived(grid.split('\n').filter(Boolean));
	const path = $derived(
		rows
			.flatMap((row, y) => [...row].map((cell, x) => (cell === '1' ? `M${x} ${y}h1v1h-1z` : '')))
			.join('')
	);
	const span = $derived(rows.length + QUIET * 2);
</script>

<svg
	viewBox="{-QUIET} {-QUIET} {span} {span}"
	role="img"
	aria-label={label}
	shape-rendering="crispEdges"
	class="block aspect-square w-full rounded-sm"
>
	<rect x={-QUIET} y={-QUIET} width={span} height={span} fill="#fff" />
	<path d={path} fill="#000" />
</svg>
