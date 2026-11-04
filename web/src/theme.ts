import { Button, createTheme, Modal, NativeSelect, PasswordInput, Select, Textarea, TextInput } from '@mantine/core';

export const theme = createTheme({
  components: {
    // 16px input text stops iOS Safari from zooming in when a field is focused,
    // and the taller fields are easier to tap.
    TextInput: TextInput.extend({ defaultProps: { size: 'md' } }),
    PasswordInput: PasswordInput.extend({ defaultProps: { size: 'md' } }),
    Textarea: Textarea.extend({ defaultProps: { size: 'md' } }),
    Select: Select.extend({ defaultProps: { size: 'md' } }),
    NativeSelect: NativeSelect.extend({ defaultProps: { size: 'md' } }),
    Button: Button.extend({ defaultProps: { size: 'md' } }),
    Modal: Modal.extend({ defaultProps: { centered: true } }),
  },
});
