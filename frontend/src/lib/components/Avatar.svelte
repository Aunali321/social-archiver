<script lang="ts">
	import { authorHue } from '$lib/format';

	/** The archived picture when there is one; otherwise a colored initial whose hue is stable
	 * per name, so an account without a picture is still recognisable at a glance. */
	interface Props {
		name: string;
		src?: string | null;
		size?: number; // px
	}

	let { name, src = null, size = 40 }: Props = $props();

	let failed = $state(false);
	const initial = $derived((name.match(/[\p{L}\p{N}]/u)?.[0] ?? '?').toUpperCase());
	const hue = $derived(authorHue(name));
</script>

{#if src && !failed}
	<img
		{src}
		alt=""
		width={size}
		height={size}
		loading="lazy"
		decoding="async"
		onerror={() => (failed = true)}
		class="shrink-0 rounded-full bg-surface-container-high object-cover"
		style="width: {size}px; height: {size}px"
	/>
{:else}
	<span
		class="flex shrink-0 items-center justify-center rounded-full font-medium text-white select-none"
		style="width: {size}px; height: {size}px; font-size: {size * 0.42}px;
			background: oklch(0.55 0.11 {hue})"
		aria-hidden="true"
	>
		{initial}
	</span>
{/if}
