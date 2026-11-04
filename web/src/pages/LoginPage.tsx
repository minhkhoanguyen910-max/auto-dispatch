import { Alert, Button, Center, Paper, PasswordInput, Stack, TextInput, Title } from '@mantine/core';
import { useForm } from '@mantine/form';
import { Navigate, useLocation } from 'react-router-dom';
import { useLogin, useMe } from '../auth';

export function LoginPage() {
  const { data: me } = useMe();
  const login = useLogin();
  const location = useLocation();
  const form = useForm({
    initialValues: { email: '', password: '' },
    validate: {
      email: (v) => (v.trim() ? null : 'Enter your email'),
      password: (v) => (v ? null : 'Enter your password'),
    },
  });

  if (me) return <Navigate to={(location.state as { from?: string } | null)?.from ?? '/'} replace />;

  return (
    <Center mih="100vh" p="md">
      <Paper withBorder shadow="sm" p={{ base: 'lg', sm: 'xl' }} radius="md" w="100%" maw={380}>
        <form onSubmit={form.onSubmit((values) => login.mutate({ ...values, email: values.email.trim() }))}>
          <Stack>
            <Title order={3}>Dispatch login</Title>
            {login.error && <Alert color="red">{login.error.message}</Alert>}
            <TextInput label="Email" type="email" autoComplete="username" autoFocus {...form.getInputProps('email')} />
            <PasswordInput label="Password" autoComplete="current-password" {...form.getInputProps('password')} />
            <Button type="submit" loading={login.isPending} fullWidth>
              Log in
            </Button>
          </Stack>
        </form>
      </Paper>
    </Center>
  );
}
