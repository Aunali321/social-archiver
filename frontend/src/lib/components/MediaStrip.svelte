<script lang="ts">
	import ImageOff from '@lucide/svelte/icons/image-off';
	import Film from '@lucide/svelte/icons/film';
	import Music from '@lucide/svelte/icons/music';
	import FileText from '@lucide/svelte/icons/file-text';
	import { api, type Item, type MediaRef } from '$lib/api';

	/** Media at reading size, like the platforms show it: one file at its natural aspect
	 * (height-capped), several in a two-column grid. Files not on disk stay small placeholder
	 * tiles — absence shouldn't dominate the layout. */
	interface Props {
		item: Item;
		/** Detail views: show every file and give videos controls */
		large?: boolean;
	}

	let { item, large = false }: Props = $props();

	const available = $derived(item.media.filter((media) => media.available));
	const missing = $derived(item.media.length - available.length);
	const visible = $derived(large ? available : available.slice(0, 4));
	const overflow = $derived(available.length - visible.length);
	const single = $derived(visible.length === 1);

	const isImage = (media: MediaRef) =>
		media.type == null ||
		media.type.includes('image') ||
		media.type.includes('photo') ||
		media.type.includes('sticker');
	const isVideo = (media: MediaRef) => !!media.type && (media.type.includes('video') || media.type.includes('gif'));
	const isAudio = (media: MediaRef) => !!media.type && media.type.includes('audio');

	function placeholderIcon(type: string | null) {
		if (type?.includes('video') || type?.includes('gif')) return Film;
		if (type?.includes('audio')) return Music;
		if (type?.includes('document')) return FileText;
		return ImageOff;
	}

	const src = (media: MediaRef) => api.mediaUrl(item.platform, item.item_id, media.index);
	const alt = (media: MediaRef) => `${media.type ?? 'media'} ${media.index + 1} from ${item.author_username}`;
</script>

{#if available.length}
	<div class={single ? '' : 'grid grid-cols-2 gap-0.5 overflow-hidden rounded-md'}>
		{#each visible as media, position (media.index)}
			{#if isAudio(media)}
				<audio src={src(media)} controls class="w-full {single ? '' : 'col-span-2'}"></audio>
			{:else if isVideo(media)}
				<!-- svelte-ignore a11y_media_has_caption -->
				<video
					src={src(media)}
					controls={large || single}
					preload="metadata"
					class={single
						? 'max-h-[30rem] w-full rounded-md bg-surface-container-highest'
						: 'aspect-[4/3] h-full w-full bg-surface-container-highest object-cover'}
				></video>
			{:else if isImage(media)}
				{#if single}
					<img
						src={src(media)}
						alt={alt(media)}
						loading="lazy"
						class="max-h-[30rem] max-w-full rounded-md bg-surface-container-highest"
					/>
				{:else}
					<div class="relative">
						<img
							src={src(media)}
							alt={alt(media)}
							loading="lazy"
							class="aspect-[4/3] h-full w-full bg-surface-container-highest object-cover"
						/>
						{#if overflow > 0 && position === visible.length - 1}
							<span
								class="absolute inset-0 flex items-center justify-center bg-scrim/50 text-title-lg text-white"
							>
								+{overflow}
							</span>
						{/if}
					</div>
				{/if}
			{:else}
				<span
					class="flex items-center justify-center bg-surface-container-highest text-on-surface-variant {single
						? 'h-28 w-full rounded-md'
						: 'aspect-[4/3]'}"
					title={media.type ?? 'file'}
				>
					{#if placeholderIcon(media.type)}
						{@const Icon = placeholderIcon(media.type)}<Icon size={22} />
					{/if}
				</span>
			{/if}
		{/each}
	</div>
{/if}

{#if missing > 0}
	<div class="mt-1 flex gap-1.5">
		{#each item.media.filter((media) => !media.available) as media (media.index)}
			{@const Icon = placeholderIcon(media.type)}
			<span
				class="flex size-12 items-center justify-center rounded-sm bg-surface-container-highest text-on-surface-variant"
				title="{media.type ?? 'media'} — not on disk"
			>
				<Icon size={16} />
			</span>
		{/each}
	</div>
{/if}
