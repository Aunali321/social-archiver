/** Theme choice: system (default), light, dark. Persisted; applied via data-theme on <html>. */

export type Theme = 'system' | 'light' | 'dark';

const stored = typeof localStorage !== 'undefined' ? localStorage.getItem('theme') : null;

const state = $state({ theme: (stored === 'light' || stored === 'dark' ? stored : 'system') as Theme });

export function theme(): Theme {
	return state.theme;
}

export function setTheme(next: Theme) {
	state.theme = next;
	if (next === 'system') {
		localStorage.removeItem('theme');
		delete document.documentElement.dataset.theme;
	} else {
		localStorage.setItem('theme', next);
		document.documentElement.dataset.theme = next;
	}
}

export function cycleTheme() {
	const order: Theme[] = ['system', 'light', 'dark'];
	setTheme(order[(order.indexOf(state.theme) + 1) % order.length]);
}
