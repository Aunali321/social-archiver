<script lang="ts">
	import Check from '@lucide/svelte/icons/check';
	import type { Snippet } from 'svelte';

	interface Props {
		selected?: boolean;
		onclick?: () => void;
		children: Snippet;
	}

	let { selected = false, onclick, children }: Props = $props();
</script>

<button
	class="state-layer inline-flex h-8 shrink-0 cursor-pointer items-center gap-1.5 rounded-sm border px-3 text-label-lg transition-colors {selected
		? 'border-transparent bg-secondary-container text-on-secondary-container'
		: 'border-outline-variant bg-transparent text-on-surface-variant'}"
	aria-pressed={selected}
	{onclick}
>
	{#if selected}<Check size={16} />{/if}
	{@render children()}
</button>
