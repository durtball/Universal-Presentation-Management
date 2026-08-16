import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";

export interface ThemedSelectOption {
  label: string;
  value: string;
  group?: string;
}

export function ThemedSelect({ value, options, onChange, disabled = false, placeholder = "Select an option" }: {
  value: string;
  options: readonly ThemedSelectOption[];
  onChange: (value: string) => void;
  disabled?: boolean;
  placeholder?: string;
}) {
  const id = useId();
  const root = useRef<HTMLDivElement>(null);
  const selectedIndex = options.findIndex(option => option.value === value);
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(Math.max(selectedIndex, 0));
  const selected = options[selectedIndex];

  useEffect(() => {
    if (!open) return;
    const dismiss = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", dismiss);
    return () => document.removeEventListener("pointerdown", dismiss);
  }, [open]);

  const choose = (index: number) => {
    const option = options[index];
    if (!option) return;
    onChange(option.value);
    setActiveIndex(index);
    setOpen(false);
  };
  const move = (direction: 1 | -1) => {
    const next = Math.min(Math.max((open ? activeIndex : Math.max(selectedIndex, 0)) + direction, 0), options.length - 1);
    setActiveIndex(next); setOpen(true);
  };
  const keyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault(); move(event.key === "ArrowDown" ? 1 : -1);
    } else if (event.key === "Home" || event.key === "End") {
      event.preventDefault(); setActiveIndex(event.key === "Home" ? 0 : options.length - 1); setOpen(true);
    } else if (event.key === "Enter" || event.key === " ") {
      event.preventDefault(); if (open) choose(activeIndex); else { setActiveIndex(Math.max(selectedIndex, 0)); setOpen(true); }
    } else if (event.key === "Escape" && open) {
      event.preventDefault(); setOpen(false);
    }
  };

  return <div className={`themed-select${open ? " themed-select--open" : ""}`} ref={root}>
    <button className="input themed-select__control" type="button" disabled={disabled}
      aria-haspopup="listbox" aria-expanded={open} aria-controls={`${id}-listbox`}
      aria-activedescendant={open ? `${id}-option-${activeIndex}` : undefined}
      onClick={()=>{setActiveIndex(Math.max(selectedIndex, 0));setOpen(current=>!current);}} onKeyDown={keyDown}>
      <span className={selected ? "" : "themed-select__placeholder"}>{selected?.label ?? placeholder}</span>
      <span className="themed-select__arrow" aria-hidden="true"/>
    </button>
    {open ? <div className="themed-select__menu" id={`${id}-listbox`} role="listbox">
      {options.map((option, index)=><div key={`${option.value}-${index}`}>
        {option.group && options[index-1]?.group !== option.group ? <div className="themed-select__group" aria-hidden="true">{option.group}</div> : null}
        <div id={`${id}-option-${index}`} role="option" aria-selected={option.value===value}
          data-value={option.value}
          className={`themed-select__option${index===activeIndex ? " themed-select__option--active" : ""}`}
          onPointerMove={()=>setActiveIndex(index)} onMouseDown={event=>event.preventDefault()} onClick={()=>choose(index)}>
          {option.label}
        </div>
      </div>)}
    </div> : null}
  </div>;
}
