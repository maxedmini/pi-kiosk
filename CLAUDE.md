# Pi kiosk — notes for Claude

**This repo is PUBLIC** (it must be: new Pis download it without a login on first boot). Never commit
passwords, keys or tokens — logins go in the gitignored `LOGINS.local.md`.
The Pis push automatic 'Update kiosk backup' commits (`backups/pi-kiosk-state.tgz`), so always
`git pull --rebase` before pushing. Running Pis never pull code; only freshly prepared SD cards do.
Multi-Mac rules: `~/OddFox/oddfox-workspace/README.md` — work only in `~/OddFox`, pull before working, commit + push after every change.
