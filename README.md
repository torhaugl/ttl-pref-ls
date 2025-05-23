ttl-pref-ls
===========

A lightweight **Language Server for Turtle (`*.ttl`) files** that replaces
opaque UUID-style IRIs with their human-friendly `skos:prefLabel`s inside
Neovim. Hovering over any IRI defined in the same file shows its label; missing
labels are flagged as diagnostics. This language server also supports inlay
hints.

## Install

Install as a python package, e.g.,
```sh
pip install git+https://github.com/torhaugl/ttl-pref-ls.git
```
Ideally, install it with `pipx` so that the command is always available, even
from virtual environments.

## Neovim setup

As of Neovim 0.11, language servers can be configured by making a file
`.config/nvim/lsp/ttl_pref_ls.lua` with this snippet inside:
```lua
return {
  cmd = { 'ttl-pref-ls' },
  filetypes = { 'turtle' },
}
```
In addition, you can enable your Neovim to start the LSP whenerver it enters a
`.nt` file with this snippet in your `init.lua` or equivalent:
```lua
vim.lsp.enable { 'nt_pref_ls' }
```

The hover functionality is accessed with 'K' in Normal mode by default. In
addition, it is useful to add keybinds to enable hints or virtual lines:
```lua
-- Toggle virtual hints and lines
vim.keymap.set('n', '<leader>th', function()
  vim.lsp.inlay_hint.enable(not vim.lsp.inlay_hint.is_enabled { bufnr = 0 })
end, { desc = '[T]oggle Inlay [H]ints' })

vim.keymap.set('n', '<leader>tv', function()
  local current = vim.diagnostic.config().virtual_lines
  vim.diagnostic.config { virtual_lines = not current }
end, { desc = '[T]oggle [V]irtual Lines' })

```

## Example

Hover tooltips and inline inlay‑hints for two labelled UUID IRIs; the
unlabelled one is underlined as a Hint diagnostic.
Inlay hints show up next to the IRI with the prefLabel.

![Hover and diagnostics](/figs/neovim.png)

The functionality also works with external prefixes, as long as there is an
internet connection. This is very useful for ontologies that uses UUIDs for
URIs like [EMMO](http://www.w3id.org/emmo#).

![Hover in EMMO](/figs/emmo.png)

## AI Attribution

Large parts of this code is automatically generated with an LLM (ChatGPT-o3)
and then refined by hand.
