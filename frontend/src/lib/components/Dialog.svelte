<script lang="ts">
	import type { Snippet } from 'svelte';

	interface Props {
		open: boolean;
		title: string;
		onclose: () => void;
		children: Snippet;
		actions?: Snippet;
	}

	let { open, title, onclose, children, actions }: Props = $props();

	let element: HTMLDialogElement | undefined = $state();

	$effect(() => {
		if (!element) return;
		if (open && !element.open) element.showModal();
		else if (!open && element.open) element.close();
	});
</script>

<dialog
	bind:this={element}
	onclose={onclose}
	onclick={(event) => {
		if (event.target === element) onclose();
	}}
	class="m-auto w-[min(92vw,26rem)] rounded-xl bg-surface-container-high p-6 text-on-surface shadow-e3 backdrop:bg-scrim/40"
>
	<h2 class="mb-4 text-title-lg">{title}</h2>
	<div class="text-body text-on-surface-variant">{@render children()}</div>
	{#if actions}
		<div class="mt-6 flex justify-end gap-2">{@render actions()}</div>
	{/if}
</dialog>
