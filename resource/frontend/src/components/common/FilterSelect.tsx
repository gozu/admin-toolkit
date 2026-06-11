/* eslint-disable react-refresh/only-export-components -- shared filter component + theme constant live together by design */
import Select, { type MultiValue, type Props as SelectProps } from 'react-select';

export type SelectOption = { value: string; label: string };

// react-select theming to match the admin-toolkit dark surface — unstyled + Tailwind classNames.
export const filterSelectClassNames: SelectProps<SelectOption, true>['classNames'] = {
  control: ({ isFocused }) =>
    `min-h-[32px] rounded-md border bg-[var(--bg-glass)] text-sm transition-colors ${
      isFocused
        ? 'border-[var(--neon-cyan)]'
        : 'border-[var(--border-glass)] hover:border-[var(--text-tertiary)]'
    }`,
  valueContainer: () => 'px-2 py-0.5 gap-1',
  placeholder: () => 'text-[var(--text-muted)] text-xs',
  input: () => 'text-[var(--text-primary)] text-xs',
  singleValue: () => 'text-[var(--text-primary)] text-xs',
  multiValue: () =>
    'bg-[var(--neon-cyan)]/15 border border-[var(--neon-cyan)]/40 rounded-sm overflow-hidden',
  multiValueLabel: () => 'text-[var(--neon-cyan)] text-[10px] font-medium px-1.5 py-0.5',
  multiValueRemove: () =>
    'text-[var(--neon-cyan)] hover:bg-[var(--neon-cyan)]/30 hover:text-white px-1',
  indicatorsContainer: () => 'pr-1',
  dropdownIndicator: ({ isFocused }) =>
    `p-1 ${isFocused ? 'text-[var(--neon-cyan)]' : 'text-[var(--text-tertiary)]'} hover:text-[var(--neon-cyan)]`,
  clearIndicator: () => 'p-1 text-[var(--text-tertiary)] hover:text-[var(--neon-red)]',
  indicatorSeparator: () => 'bg-[var(--border-glass)]',
  menu: () =>
    'mt-1 rounded-md border border-[var(--border-glass)] bg-[var(--bg-elevated)] shadow-lg overflow-hidden',
  menuList: () => 'py-1 max-h-[260px]',
  option: ({ isFocused, isSelected }) =>
    `px-2.5 py-1.5 text-xs cursor-pointer ${
      isSelected
        ? 'bg-[var(--neon-cyan)]/20 text-[var(--neon-cyan)]'
        : isFocused
          ? 'bg-[var(--bg-glass-hover)] text-[var(--text-primary)]'
          : 'text-[var(--text-secondary)]'
    }`,
  noOptionsMessage: () => 'px-2.5 py-2 text-xs text-[var(--text-muted)]',
};

export function FilterField({
  label,
  options,
  value,
  onChange,
  placeholder,
}: {
  label: string;
  options: SelectOption[];
  value: MultiValue<SelectOption>;
  onChange: (next: MultiValue<SelectOption>) => void;
  placeholder: string;
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-[10px] uppercase tracking-wide text-[var(--text-muted)]">{label}</span>
      <Select
        isMulti
        unstyled
        // Stable class hook for the menu entrance animation (.adk-select__menu).
        classNamePrefix="adk-select"
        options={options}
        value={value}
        onChange={(next) => onChange(next)}
        placeholder={placeholder}
        closeMenuOnSelect={false}
        hideSelectedOptions={false}
        classNames={filterSelectClassNames}
        // react-select hard-codes an inline `z-index: 1` on the menu even in
        // `unstyled` mode, which loses to the sticky table header (z-index: 10).
        // Inline styles beat classes, so the menu z-index can only be raised here.
        styles={{ menu: (base) => ({ ...base, zIndex: 30 }) }}
      />
    </label>
  );
}
