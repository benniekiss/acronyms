--[[
    Lua Filter to parse acronyms in a Markdown document.

    Acronyms must be in the form `\acr{key}` where key is the acronym key.
    The first occurrence of an acronym is replaced by its long name, as
    defined by a list of acronyms in the document's metadata.
    Other occurrences are simply replaced by the acronym's short name.

    A List of Acronym is also generated (similar to a Glossary in LaTeX),
    and all occurrences contain a link to the acronym's definition in this
    List.
]]

local script_dir = debug.getinfo(1, "S").source:match("@(.*/)")
package.path = script_dir .. "?.lua;" .. package.path

-- Some helper functions
local Helpers = require("acronyms_helpers")

-- The Acronyms database
local Acronyms = require("acronyms")

-- Replacement function
local AcronymsPandoc = require("acronyms_pandoc")

-- The options for the List Of Acronyms, as defined in the document's metadata.
local Options = require("acronyms_options")

-- Parse options within old style
local function parse_opts(opts_str)
  local opts = {}
  if not opts_str or opts_str == "" then return opts end
  for entry in opts_str:gmatch("([^,]+)") do
    entry = entry:match("^%s*(.-)%s*$")  -- trim
    if entry ~= "" then
      local k, v = entry:match("^([%w_%-]+)%s*=%s*(.-)$")
      if k then
        v = v:match("^%s*(.-)%s*$")      -- trim
        -- strip optional quotes
        v = v:gsub('^"(.*)"$', "%1"):gsub("^'(.*)'$", "%1")
        opts[k] = v
      else
        opts[entry] = true   -- bare flag
      end
    end
  end
  return opts
end

--[[
    Parse the document's metadata, including options, and acronyms' definitions.
    It does not change the metadata.
--]]
function Meta(m)
    Options:parseOptionsFromMetadata(m)

    -- Parse acronyms directly from the metadata (`acronyms.keys`)
    Acronyms:parseFromMetadata(m, Options["on_duplicate"])

    -- Parse acronyms from external files
    if (m and m.acronyms and m.acronyms.fromfile) then
        if Helpers.isMetaList(m.acronyms.fromfile) then
            -- We have several files to read
            for _, filepath in ipairs(m.acronyms.fromfile) do
                filepath = pandoc.utils.stringify(filepath)
                Acronyms:parseFromYamlFile(filepath, Options["on_duplicate"])
            end
        else
            -- We have a single file
            local filepath = pandoc.utils.stringify(m.acronyms.fromfile)
            Acronyms:parseFromYamlFile(filepath, Options["on_duplicate"])
        end
    end

    return nil
end


--[[
    Append the List Of Acronyms to the document (at the beginning).
--]]
local function appendLoA(doc)
    local pos
    if not Options["insert_loa"] then
        -- If disabled, do nothing
        return nil
    elseif Options["insert_loa"] == "beginning" then
        -- Insert at the first block in the document
        pos = 1
    elseif Options["insert_loa"] == "end" then
        -- Insert at the last block in the document
        pos = #doc.blocks + 1
    else
        pandoc.log.warn(
            "[acronyms] Unrecognized option `insert_loa`=`"
            .. tostring(Options["insert_loa"])
            .. "` in `appendLoA`."
        )
        assert(false)
    end

    local header, definition_list = AcronymsPandoc.generateLoA()

    -- Insert the DefinitionList
    table.insert(doc.blocks, pos, definition_list)

    -- Insert the Header
    if header ~= nil then
        table.insert(doc.blocks, pos, header)
    end

    return pandoc.Pandoc(doc.blocks, doc.meta)
end


--[[
    Place the List of Acronyms in the document, in place of a `\printacronysm`.

    This is used when the user wants to generate the LoA at a very specific
    place (instead of "simply" at the beginning or end).
    Because the Header (title) and DefinitionList (LoA itself) are Blocks,
    we must replace a Block as well (Pandoc does not allow to create Blocks
    from Inlines). Thus, `\printacronyms` needs to be in its own Block (no
    other text!).
--]]
function RawBlock(el)
    -- The block's content must be exactly "\printacronyms"
    if not (el and el.text == "\\printacronyms") then
        return nil
    end

    local header, definition_list = AcronymsPandoc.generateLoA()

    if header ~= nil then
        return { header, definition_list }
    else
        return definition_list
    end
end


-- Match \acr{key}, \acrs{key}, or with an option: \acr[opt]{key}, \acrs[opt]{key}
local LATEX_PATTERN = "\\(acrs?)%[?(.-)%]?{(.+)}"
-- Match +key (singular), *key (plural), or with an option: +key{opt}, *key{opt}
local MD_PATTERN = "([%+%*])(%w+){?(.-)}?"
-- Match KEY
local BASIC_PATTERN = "^(%u+)$"


local function parseKey(text)
    local command, key, opts, plural

    if Options.format == "latex" then
        command, opts, key = string.match(text, LATEX_PATTERN)
        plural = command and (command:sub(-1) == "s")
    elseif Options.format == "markdown" then
        command, key, opts = string.match(text, MD_PATTERN)
        plural = command and (command == "*")
    elseif Options.format == "basic" then
        key = string.match(text, BASIC_PATTERN)
        plural = false
    end

    return key, opts, plural
end


--[[
Replace each `\acr{KEY}` (or `\acr[opt]{KEY}`) with the correct text and link to the list of acronyms.
--]]
local function replaceAcronym(el)
    local key, opts, plural = parseKey(el.text)

    if key then
        -- parse the options
        opts = parse_opts(opts)

        if Acronyms:contains(key) then
            local style = opts.style or nil

            local insert_links = nil
            if opts.insert_links ~= nil then
              insert_links = Helpers.str_to_boolean(opts.insert_links)
            end

            local is_first_use = nil
            if opts.first_use ~= nil then
              is_first_use = Helpers.str_to_boolean(opts.first_use)
            end

            plural = plural or (opts.plural == "true" or opts.plural == true)

            local case_target = opts.case_target

            local case = opts.case

            return AcronymsPandoc.replaceExistingAcronym(
                key, style, is_first_use, insert_links, plural, case_target, case
            )
        else
            -- The acronym does not exists
            local non_existing = opts.non_existing or nil
            return AcronymsPandoc.replaceNonExistingAcronym(key, non_existing)
        end
    end

    -- This is not an acronym, return nil to leave it unchanged.
    return nil
end


-- Force the execution of the Meta filter before the RawInline
-- (we need to load the acronyms first!)
-- RawBlock and Doc happen after RawInline so that the actual usage order
-- of acronyms is known (and we can sort the List of Acronyms accordingly)
return {
    { Meta = Meta },
    { RawInline = replaceAcronym },
    { Str = replaceAcronym },
    { RawBlock = RawBlock },
    { Pandoc = appendLoA },
}
