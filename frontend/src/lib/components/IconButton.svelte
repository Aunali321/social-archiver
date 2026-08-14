<script lang="ts">
	import type { Snippet } from 'svelte';
	import type { HTMLButtonAttributes } from 'svelte/elements';

	interface Props extends HTMLButtonAttributes {
		label: string;
		selected?: boolean;
		children: Snippet;
	}

	let { label, selected = false, children, class: className = '', ...rest }: Props = $props();
</script>

<button
	aria-label={label}
	title={label}
	class="state-layer inline-flex size-10 shrink-0 cursor-pointer items-center justify-center rounded-full transition-colors disabled:cursor-not-allowed disabled:opacity-40 {selected
		? 'bg-primary-container text-on-primary-container'
		: 'text-on-surface-variant'} {className}"
	{...rest}
>
	{@render children()}
</button>
