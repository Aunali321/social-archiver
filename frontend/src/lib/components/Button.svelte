<script lang="ts">
	import type { Snippet } from 'svelte';
	import type { HTMLButtonAttributes } from 'svelte/elements';

	interface Props extends HTMLButtonAttributes {
		variant?: 'filled' | 'tonal' | 'outlined' | 'text';
		children: Snippet;
	}

	let { variant = 'filled', children, class: className = '', ...rest }: Props = $props();

	const styles: Record<string, string> = {
		filled: 'bg-primary text-on-primary',
		tonal: 'bg-secondary-container text-on-secondary-container',
		outlined: 'border border-outline text-primary bg-transparent',
		text: 'text-primary bg-transparent'
	};
</script>

<button
	class="state-layer inline-flex h-10 shrink-0 cursor-pointer items-center justify-center gap-2 rounded-full px-6 text-label-lg transition-shadow disabled:cursor-not-allowed disabled:opacity-40 {styles[
		variant
	]} {className}"
	{...rest}
>
	{@render children()}
</button>
