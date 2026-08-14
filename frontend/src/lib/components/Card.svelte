<script lang="ts">
	import type { Snippet } from 'svelte';

	interface Props {
		variant?: 'elevated' | 'filled' | 'outlined';
		href?: string;
		onclick?: () => void;
		class?: string;
		children: Snippet;
	}

	let { variant = 'filled', href, onclick, class: className = '', children }: Props = $props();

	const styles: Record<string, string> = {
		elevated: 'bg-surface-container-low shadow-e1',
		filled: 'bg-surface-container',
		outlined: 'border border-outline-variant bg-surface'
	};
	const base = 'block rounded-md text-left';
</script>

{#if href}
	<a {href} class="{base} state-layer {styles[variant]} {className}">{@render children()}</a>
{:else if onclick}
	<button {onclick} class="{base} state-layer w-full cursor-pointer {styles[variant]} {className}">
		{@render children()}
	</button>
{:else}
	<div class="{base} {styles[variant]} {className}">{@render children()}</div>
{/if}
