import { useState, useEffect, useRef } from "react";

interface SearchBarProps {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  debounceMs?: number;
}

const inputStyle: React.CSSProperties = {
  width: "100%",
  padding: "10px 16px",
  fontSize: 16,
  border: "1.5px solid #d0d5e8",
  borderRadius: 8,
  outline: "none",
  background: "#fff",
  boxSizing: "border-box",
  transition: "border-color 0.15s",
};

export default function SearchBar({
  value,
  onChange,
  placeholder = "Search files...",
  debounceMs = 350,
}: SearchBarProps) {
  const [localValue, setLocalValue] = useState(value);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    setLocalValue(value);
  }, [value]);

  function handleChange(e: React.ChangeEvent<HTMLInputElement>) {
    const newValue = e.target.value;
    setLocalValue(newValue);
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => {
      onChange(newValue);
    }, debounceMs);
  }

  useEffect(() => {
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, []);

  return (
    <input
      type="search"
      value={localValue}
      onChange={handleChange}
      placeholder={placeholder}
      style={inputStyle}
    />
  );
}
