'use strict';
// Nocturne impact check: symbols and identifier uses from tree-sitter, for nocturne_hook.py.
// Reads {"jobs": [{"path", "lang", "names": [..] | null, "skip": [[startRow, endRow], ..]}]} on stdin and writes
// {"results": [{"symbols": [..], "uses": [[row, kind], ..]} | {"error": ".."}]}. Rows are 0-based. Only identifier nodes
// count as uses, so a name in a comment or a string never does.
const fs = require('fs');
const path = require('path');
const { Parser, Language } = require('./tree-sitter.cjs');

const FAMILY = { python: 'python', javascript: 'js', typescript: 'js', tsx: 'js', go: 'go', rust: 'rust', java: 'java' };
const DEFINITIONS = {
  python: { function_definition: 'function', class_definition: 'class' },
  js: {
    function_declaration: 'function', generator_function_declaration: 'function', class_declaration: 'class',
    abstract_class_declaration: 'class', method_definition: 'method', interface_declaration: 'type',
    type_alias_declaration: 'type', enum_declaration: 'type', variable_declarator: 'variable',
  },
  go: { function_declaration: 'function', method_declaration: 'method', type_spec: 'type' },
  rust: { function_item: 'function', struct_item: 'type', enum_item: 'type', trait_item: 'type', type_item: 'type' },
  java: {
    class_declaration: 'class', record_declaration: 'class', interface_declaration: 'type', enum_declaration: 'type',
    method_declaration: 'method', constructor_declaration: 'method',
  },
};
const CLASSES = new Set(['class_definition', 'class_declaration', 'abstract_class_declaration', 'class', 'record_declaration',
  'interface_declaration', 'enum_declaration', 'impl_item', 'trait_item']);
const IDENTIFIERS = new Set(['identifier', 'property_identifier', 'shorthand_property_identifier', 'shorthand_property_identifier_pattern',
  'type_identifier', 'field_identifier']);
const IMPORTS = new Set(['import_statement', 'import_from_statement', 'import_declaration', 'import_spec', 'use_declaration',
  'future_import_statement']);
const CALLS = { call_expression: 'function', call: 'function', new_expression: 'constructor' };
const MEMBERS = { member_expression: 'property', attribute: 'attribute', field_expression: 'field', selector_expression: 'field',
  scoped_identifier: 'name', field_access: 'field' };
const FUNCTION_VALUES = new Set(['arrow_function', 'function_expression', 'function', 'generator_function']);

function same(a, b) {
  return a && b && a.startIndex === b.startIndex && a.endIndex === b.endIndex && a.type === b.type;
}

function walk(root, visit) {
  const stack = [root];
  while (stack.length) {
    const node = stack.pop();
    visit(node);
    for (let i = node.namedChildCount - 1; i >= 0; i--) stack.push(node.namedChild(i));
  }
}

function ancestors(node) {
  const found = [];
  for (let p = node.parent; p; p = p.parent) found.push(p);
  return found;
}

function owner(node) {
  // The class, impl or trait a definition sits in, and its name.
  for (const p of ancestors(node)) {
    if (!CLASSES.has(p.type)) continue;
    const name = p.childForFieldName('name') || p.childForFieldName('type');
    return name ? name.text.replace(/<.*$/s, '') : null;
  }
  return null;
}

function jsExports(root) {
  const names = new Set();
  walk(root, (node) => {
    if (node.type === 'export_specifier') {
      const name = node.childForFieldName('name');
      if (name) names.add(name.text);
    } else if (node.type === 'assignment_expression') {
      const left = node.childForFieldName('left');
      const right = node.childForFieldName('right');
      if (!left) return;
      const text = left.text;
      const match = /^(?:module\.)?exports\.([A-Za-z_$][\w$]*)$/.exec(text);
      if (match) names.add(match[1]);
      if (text === 'module.exports' && right && right.type === 'object') {
        for (let i = 0; i < right.namedChildCount; i++) {
          const item = right.namedChild(i);
          const key = item.type === 'pair' ? item.childForFieldName('key') : item;
          if (key) names.add(key.text);
        }
      }
    }
  });
  return names;
}

function symbolsOf(root, family) {
  const table = DEFINITIONS[family];
  const exportNames = family === 'js' ? jsExports(root) : new Set();
  const symbols = [];
  walk(root, (node) => {
    let kind = table[node.type];
    if (!kind) return;
    const nameNode = node.childForFieldName('name');
    if (!nameNode) return;
    const name = nameNode.text;
    let start = node.startPosition.row;
    let end = node.endPosition.row;
    let parent = null;
    let exported = false;
    const up = ancestors(node);
    if (family === 'js' && node.type === 'variable_declarator') {
      // Only module-level declarations: `const handler = () => ...` is a function, other values are variables.
      const declaration = node.parent;
      const holder = declaration && declaration.parent;
      if (!holder || !['program', 'export_statement'].includes(holder.type)) return;
      const value = node.childForFieldName('value');
      kind = value && FUNCTION_VALUES.has(value.type) ? 'function' : 'variable';
      start = declaration.startPosition.row;
      end = declaration.endPosition.row;
    }
    if (family === 'python') {
      if (node.parent && node.parent.type === 'decorated_definition') start = node.parent.startPosition.row;
      parent = owner(node);
      if (kind === 'function' && parent && up.some((p) => p.type === 'class_definition')) kind = 'method';
      const topLevel = !up.some((p) => p.type === 'function_definition' || p.type === 'class_definition');
      exported = !name.startsWith('_') && (topLevel || (kind === 'method' && name.length >= 6));
    } else if (family === 'js') {
      if (kind === 'method') parent = owner(node);
      exported = up.some((p) => p.type === 'export_statement') || (exportNames.has(name) && !parent) || (kind === 'method' && name.length >= 6);
      if (node.parent && node.parent.type === 'export_statement') start = node.parent.startPosition.row;
    } else if (family === 'go') {
      if (kind === 'method') {
        const receiver = node.childForFieldName('receiver');
        const type = receiver && /([A-Za-z_]\w*)\s*\)\s*$/.exec(receiver.text.replace(/\[.*?\]/g, ''));
        parent = type ? type[1] : null;
      }
      // Code in the same package uses unexported names from other files, so every top-level name is worth looking for.
      exported = kind !== 'method' || name.length >= 6 || /^[A-Z]/.test(name);
    } else if (family === 'rust') {
      parent = owner(node);
      if (kind === 'function' && parent) kind = 'method';
      const pub = node.namedChildren.some((c) => c.type === 'visibility_modifier');
      exported = pub || (kind === 'method' && name.length >= 6);
    } else if (family === 'java') {
      parent = owner(node);
      const modifiers = node.namedChildren.find((c) => c.type === 'modifiers');
      exported = (modifiers && /\b(public|protected)\b/.test(modifiers.text)) || (kind === 'method' && name.length >= 6);
    }
    symbols.push({ name, kind, start, end: Math.max(end, start), exported, parent });
  });
  return symbols.sort((a, b) => a.start - b.start);
}

function isDefinitionName(node, family) {
  const parent = node.parent;
  if (!parent || !DEFINITIONS[family][parent.type]) return false;
  return same(parent.childForFieldName('name'), node);
}

function kindOf(node) {
  const up = ancestors(node);
  for (const p of up) {
    if (IMPORTS.has(p.type)) return 'import';
    if (p.type === 'export_statement' && p.childForFieldName('source')) return 'import';
    if (p.type === 'call_expression') {
      const fn = p.childForFieldName('function');
      if (fn && fn.text === 'require') return 'import';
    }
  }
  let target = node;
  const parent = node.parent;
  if (parent && MEMBERS[parent.type] && same(parent.childForFieldName(MEMBERS[parent.type]), node)) target = parent;
  const caller = target.parent;
  if (caller && CALLS[caller.type] && same(caller.childForFieldName(CALLS[caller.type]), target)) return 'call';
  if (caller && caller.type === 'method_invocation' && same(caller.childForFieldName('name'), node)) return 'call';
  if (parent && ['jsx_opening_element', 'jsx_self_closing_element'].includes(parent.type)) return 'call';
  return 'reference';
}

function usesOf(root, family, names, skip) {
  const wanted = new Set(names);
  const found = [];
  walk(root, (node) => {
    if (!IDENTIFIERS.has(node.type) || !wanted.has(node.text)) return;
    const row = node.startPosition.row;
    if (skip.some(([start, end]) => start <= row && row <= end)) return;
    if (isDefinitionName(node, family)) return;
    found.push([row, kindOf(node)]);
  });
  return found.sort((a, b) => a[0] - b[0]);
}

async function main() {
  const input = JSON.parse(fs.readFileSync(0, 'utf8') || '{}');
  await Parser.init({ locateFile: () => path.join(__dirname, 'tree-sitter.wasm') });
  const languages = {};
  const results = [];
  for (const job of input.jobs || []) {
    try {
      const family = FAMILY[job.lang];
      if (!family) throw new Error(`no grammar for ${job.lang}`);
      if (!languages[job.lang]) {
        languages[job.lang] = await Language.load(path.join(__dirname, 'grammars', `tree-sitter-${job.lang}.wasm`));
      }
      const parser = new Parser();
      parser.setLanguage(languages[job.lang]);
      const source = fs.readFileSync(job.path, 'utf8').replace(/\r\n/g, '\n');
      const tree = parser.parse(source);
      const result = { symbols: symbolsOf(tree.rootNode, family) };
      if (job.names && job.names.length) result.uses = usesOf(tree.rootNode, family, job.names, job.skip || []);
      tree.delete();
      parser.delete();
      results.push(result);
    } catch (error) {
      results.push({ error: String(error && error.message || error) });
    }
  }
  process.stdout.write(JSON.stringify({ results }));
}

main().catch((error) => {
  process.stderr.write(String(error && error.stack || error));
  process.exit(1);
});
