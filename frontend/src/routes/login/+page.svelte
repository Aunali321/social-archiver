<script lang="ts">
	import { onMount } from 'svelte';
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import Archive from '@lucide/svelte/icons/archive';
	import KeyRound from '@lucide/svelte/icons/key-round';
	import CircleAlert from '@lucide/svelte/icons/circle-alert';
	import { api, type Authorization } from '$lib/api';
	import TextField from '$lib/components/TextField.svelte';
	import Button from '$lib/components/Button.svelte';
	import Skeleton from '$lib/components/Skeleton.svelte';

	/** Sign-in, and the consent step of an MCP client's OAuth flow: the SDK's /authorize
	 * sends the browser here with ?authorize=<request>, and approving returns it to the client. */
	const requestId = page.url.searchParams.get('authorize');
	const next = page.url.searchParams.get('next') ?? '/';

	let stage = $state<'checking' | 'password' | 'consent'>('checking');
	let password = $state('');
	let request = $state<Authorization | null>(null);
	let busy = $state(false);
	let error = $state<string | null>(null);

	const clientHost = $derived(request ? new URL(request.redirect_uri).host : '');

	function message(e: unknown): string {
		return e instanceof Error ? e.message : String(e);
	}

	async function signedIn() {
		if (!requestId) {
			goto(next.startsWith('/') ? next : '/', { replaceState: true });
			return;
		}
		try {
			request = await api.authorization(requestId);
			stage = 'consent';
		} catch (e) {
			error = message(e);
			stage = 'consent';
		}
	}

	async function signIn(event: SubmitEvent) {
		event.preventDefault();
		busy = true;
		error = null;
		try {
			await api.login(password);
			password = '';
			await signedIn();
		} catch (e) {
			error = message(e);
		} finally {
			busy = false;
		}
	}

	async function decide(approve: boolean) {
		busy = true;
		try {
			location.assign((await api.decide(requestId!, approve)).redirect);
		} catch (e) {
			error = message(e);
			busy = false;
		}
	}

	onMount(async () => {
		if ((await api.session()).authenticated) await signedIn();
		else stage = 'password';
	});
</script>

<svelte:head><title>Sign in · Archive</title></svelte:head>

<div class="flex min-h-dvh items-center justify-center bg-surface px-4 py-10">
	<section
		aria-labelledby="login-title"
		class="w-full max-w-sm rounded-xl bg-surface-container-low p-6 sm:p-8"
	>
		<span
			class="mb-6 flex size-12 items-center justify-center rounded-lg bg-primary-container text-on-primary-container"
		>
			<Archive size={24} />
		</span>

		{#if stage === 'checking'}
			<Skeleton class="h-7 w-40" />
			<Skeleton class="mt-4 h-12 w-full rounded-full" />
		{:else if stage === 'password'}
			<h1 id="login-title" class="text-headline text-on-surface">Sign in</h1>
			<p class="mt-2 text-body text-on-surface-variant">
				{requestId
					? 'An app wants to connect to your archive. Sign in to review it.'
					: 'Enter the archive password to continue.'}
			</p>
			<form class="mt-6 flex flex-col gap-4" onsubmit={signIn}>
				<TextField
					label="Password"
					type="password"
					autocomplete="current-password"
					required
					autofocus
					bind:value={password}
				>
					{#snippet leading()}<KeyRound size={20} />{/snippet}
				</TextField>
				{#if error}
					<p role="alert" class="flex items-center gap-2 text-body text-error">
						<CircleAlert size={16} />{error}
					</p>
				{/if}
				<Button type="submit" disabled={busy || !password}>
					{busy ? 'Signing in…' : 'Sign in'}
				</Button>
			</form>
		{:else if request}
			<h1 id="login-title" class="text-headline text-on-surface">Connect {request.client_name}?</h1>
			<p class="mt-2 text-body text-on-surface-variant">
				It will be able to search and read everything in your archive. It cannot change anything.
				Allowing sends you back to <span class="font-medium text-on-surface">{clientHost}</span>.
			</p>
			{#if error}
				<p role="alert" class="mt-4 flex items-center gap-2 text-body text-error">
					<CircleAlert size={16} />{error}
				</p>
			{/if}
			<div class="mt-6 flex justify-end gap-2">
				<Button variant="text" disabled={busy} onclick={() => decide(false)}>Deny</Button>
				<Button disabled={busy} onclick={() => decide(true)}>Allow</Button>
			</div>
		{:else}
			<h1 id="login-title" class="text-headline text-on-surface">Can't connect</h1>
			<p role="alert" class="mt-2 text-body text-on-surface-variant">{error}</p>
		{/if}
	</section>
</div>
