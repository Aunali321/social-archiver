<script lang="ts">
	import type { ProfileVersion } from '$lib/api';
	import { displayName, formatDate, formatHandle } from '$lib/format';
	import Avatar from './Avatar.svelte';

	/** What an account showed before, newest first: renames, old bios, old pictures. The
	 * dates are when the archive saw each version, which is not when the account changed. */
	interface Props {
		platform: string;
		versions: ProfileVersion[];
	}

	let { platform, versions }: Props = $props();

	const span = (version: ProfileVersion) =>
		formatDate(version.first_seen) === formatDate(version.last_seen)
			? formatDate(version.first_seen)
			: `${formatDate(version.first_seen)} – ${formatDate(version.last_seen)}`;
</script>

<section aria-labelledby="history" class="rounded-lg bg-surface-container-low p-4">
	<h2 id="history" class="text-title text-on-surface">Earlier</h2>
	<p class="text-label text-on-surface-variant">As the archive saw it</p>
	<ol class="mt-3 flex flex-col gap-3">
		{#each versions as version, index (index)}
			<li class="flex gap-3">
				<Avatar name={displayName(version)} src={version.avatar_url} size={32} />
				<div class="min-w-0 flex-1">
					<p class="flex min-w-0 items-baseline gap-1.5 text-label-lg">
						<span class="truncate text-on-surface">{displayName(version)}</span>
						{#if version.display_name}
							<span class="truncate text-on-surface-variant">{formatHandle(platform, version.handle)}</span>
						{/if}
					</p>
					<p class="text-label text-on-surface-variant">{span(version)}</p>
					{#if version.bio}
						<p class="mt-1 line-clamp-3 text-body break-words text-on-surface-variant">{version.bio}</p>
					{/if}
				</div>
			</li>
		{/each}
	</ol>
</section>
