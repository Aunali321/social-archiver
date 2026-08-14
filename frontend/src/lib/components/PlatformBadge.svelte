<script lang="ts">
	import Bird from '@lucide/svelte/icons/bird';
	import MessageCircle from '@lucide/svelte/icons/message-circle';
	import Camera from '@lucide/svelte/icons/camera';
	import CircleDot from '@lucide/svelte/icons/circle-dot';

	interface Props {
		platform: string;
		size?: number;
	}

	let { platform, size = 16 }: Props = $props();

	const known = ['twitter', 'reddit', 'instagram', 'whatsapp'];
</script>

<span
	class="inline-flex size-7 shrink-0 items-center justify-center rounded-full bg-secondary-container text-on-secondary-container"
	style={known.includes(platform)
		? `background: var(--tint-${platform}-bg); color: var(--tint-${platform}-fg)`
		: ''}
	title={platform}
>
	{#if platform === 'twitter'}<Bird {size} />
	{:else if platform === 'whatsapp'}<MessageCircle {size} />
	{:else if platform === 'instagram'}<Camera {size} />
	{:else}<CircleDot {size} />{/if}
</span>
