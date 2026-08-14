<script lang="ts">
	import '../app.css';
	import { page } from '$app/state';
	import LayoutList from '@lucide/svelte/icons/layout-list';
	import Search from '@lucide/svelte/icons/search';
	import MessagesSquare from '@lucide/svelte/icons/messages-square';
	import ChartColumn from '@lucide/svelte/icons/chart-column';
	import Settings2 from '@lucide/svelte/icons/settings-2';
	import SunMoon from '@lucide/svelte/icons/sun-moon';
	import Sun from '@lucide/svelte/icons/sun';
	import Moon from '@lucide/svelte/icons/moon';
	import Archive from '@lucide/svelte/icons/archive';
	import { theme, cycleTheme } from '$lib/theme.svelte';
	import { snackbars } from '$lib/components/snackbar.svelte';
	import IconButton from '$lib/components/IconButton.svelte';

	let { children } = $props();

	const destinations = [
		{ href: '/', label: 'Timeline', icon: LayoutList },
		{ href: '/search', label: 'Search', icon: Search },
		{ href: '/chats', label: 'Chats', icon: MessagesSquare },
		{ href: '/stats', label: 'Stats', icon: ChartColumn },
		{ href: '/admin', label: 'Admin', icon: Settings2 }
	];

	function active(href: string): boolean {
		return href === '/' ? page.url.pathname === '/' : page.url.pathname.startsWith(href);
	}

	const themeIcons = { system: SunMoon, light: Sun, dark: Moon };
	const ThemeIcon = $derived(themeIcons[theme()]);
</script>

<div class="flex min-h-dvh">
	<!-- Nav rail (desktop) -->
	<nav
		aria-label="Main"
		class="sticky top-0 hidden h-dvh w-20 shrink-0 flex-col items-center gap-1 bg-surface pt-4 md:flex"
	>
		<span class="mb-4 flex size-10 items-center justify-center rounded-md bg-primary-container text-on-primary-container">
			<Archive size={20} />
		</span>
		{#each destinations as dest (dest.href)}
			{@const Icon = dest.icon}
			<a
				href={dest.href}
				aria-current={active(dest.href) ? 'page' : undefined}
				class="group flex w-full flex-col items-center gap-1 py-2 text-label"
			>
				<span
					class="state-layer flex h-8 w-14 items-center justify-center rounded-full transition-colors {active(
						dest.href
					)
						? 'bg-secondary-container text-on-secondary-container'
						: 'text-on-surface-variant'}"
				>
					<Icon size={20} />
				</span>
				<span class={active(dest.href) ? 'text-on-surface' : 'text-on-surface-variant'}>
					{dest.label}
				</span>
			</a>
		{/each}
		<div class="mt-auto pb-4">
			<IconButton label="Theme: {theme()}" onclick={cycleTheme}><ThemeIcon size={20} /></IconButton>
		</div>
	</nav>

	<!-- Content -->
	<main class="min-w-0 flex-1 pb-24 md:pb-6">
		{@render children()}
	</main>
</div>

<!-- Bottom navigation (mobile) -->
<nav
	aria-label="Main"
	class="fixed inset-x-0 bottom-0 z-20 flex h-20 items-start justify-around bg-surface-container pt-3 md:hidden"
>
	{#each destinations as dest (dest.href)}
		{@const Icon = dest.icon}
		<a
			href={dest.href}
			aria-current={active(dest.href) ? 'page' : undefined}
			class="flex flex-col items-center gap-1 text-label"
		>
			<span
				class="flex h-8 w-14 items-center justify-center rounded-full {active(dest.href)
					? 'bg-secondary-container text-on-secondary-container'
					: 'text-on-surface-variant'}"
			>
				<Icon size={20} />
			</span>
			<span class={active(dest.href) ? 'text-on-surface' : 'text-on-surface-variant'}>
				{dest.label}
			</span>
		</a>
	{/each}
</nav>

<!-- Snackbars -->
<div class="pointer-events-none fixed inset-x-0 bottom-24 z-30 flex flex-col items-center gap-2 md:bottom-6">
	{#each snackbars as message (message.id)}
		<output
			class="pointer-events-auto rounded-sm px-4 py-3 text-body shadow-e3 {message.error
				? 'bg-error-container text-on-error-container'
				: 'bg-inverse-surface text-inverse-on-surface'}"
		>
			{message.text}
		</output>
	{/each}
</div>
