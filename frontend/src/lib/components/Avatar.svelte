<script lang="ts">
	import { authorHue } from '$lib/format';

	/** Deterministic identicon: same author, same hue, everywhere. Avatars aren't archived,
	 * so a stable colored initial is the honest equivalent of Twitter's avatar anchor. */
	interface Props {
		name: string;
		size?: number; // px
	}

	let { name, size = 40 }: Props = $props();

	const initial = $derived((name.match(/[\p{L}\p{N}]/u)?.[0] ?? '?').toUpperCase());
	const hue = $derived(authorHue(name));
</script>

<span
	class="flex shrink-0 items-center justify-center rounded-full font-medium text-white select-none"
	style="width: {size}px; height: {size}px; font-size: {size * 0.42}px;
		background: oklch(0.55 0.11 {hue})"
	aria-hidden="true"
>
	{initial}
</span>
