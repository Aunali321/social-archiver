<script lang="ts">
	import type { Snippet } from 'svelte';
	import type { HTMLInputAttributes } from 'svelte/elements';

	interface Props extends HTMLInputAttributes {
		label: string;
		value?: string;
		leading?: Snippet;
		trailing?: Snippet;
	}

	let {
		label,
		value = $bindable(''),
		leading,
		trailing,
		class: className = '',
		...rest
	}: Props = $props();
</script>

<label
	class="flex h-12 items-center gap-3 rounded-full bg-surface-container-high px-4 text-body-lg text-on-surface transition-colors focus-within:bg-surface-container-highest {className}"
>
	{#if leading}<span class="shrink-0 text-on-surface-variant">{@render leading()}</span>{/if}
	<input
		bind:value
		aria-label={label}
		placeholder={label}
		class="min-w-0 flex-1 bg-transparent outline-none placeholder:text-on-surface-variant"
		{...rest}
	/>
	{#if trailing}<span class="shrink-0 text-on-surface-variant">{@render trailing()}</span>{/if}
</label>
