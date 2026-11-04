import { useState } from 'react';
import { ActionIcon, TextInput, Tooltip } from '@mantine/core';
import { IconCheck, IconCopy } from '@tabler/icons-react';
import { copyText } from '../lib/clipboard';

/** Read-only field with a copy button. The text can also be selected and copied by hand. */
export function CopyField({ label, value, description }: { label: string; value: string; description?: string }) {
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    if (await copyText(value)) {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    }
  };

  return (
    <TextInput
      label={label}
      description={description}
      value={value}
      readOnly
      onFocus={(e) => e.currentTarget.select()}
      rightSectionWidth={44}
      rightSection={
        <Tooltip label={copied ? 'Copied' : 'Copy'} withArrow>
          <ActionIcon variant="subtle" color={copied ? 'teal' : 'gray'} size="lg" onClick={copy} aria-label={`Copy ${label}`}>
            {copied ? <IconCheck size={18} /> : <IconCopy size={18} />}
          </ActionIcon>
        </Tooltip>
      }
    />
  );
}
