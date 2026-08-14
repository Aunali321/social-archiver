<script lang="ts">
	import type { Snippet } from 'svelte';

	interface Props {
		icon?: Snippet;
		title: string;
		detail?: string;
		error?: boolean;
	}

	let { icon, title, detail, error = false }: Props = $props();
</script>

<div class="flex flex-col items-center gap-3 px-6 py-16 text-center">
	{#if icon}
		<div
			class="flex size-14 items-center justify-center rounded-full {error
				? 'bg-error-container text-on-error-container'
				: 'bg-secondary-container text-on-secondary-container'}"
		>
			{@render icon()}
		</div>
	{/if}
	<p class="text-title text-on-surface">{title}</p>
	{#if detail}<p class="max-w-md text-body text-on-surface-variant">{detail}</p>{/if}
</div>
