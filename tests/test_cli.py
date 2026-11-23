from cli import main


def test_cli_without_args_shows_usage(capsys):
    assert main([]) == 0

    out = capsys.readouterr().out
    assert "usage:" in out
    assert "export" in out
    assert "render" in out


def test_cli_rejects_unknown_command(capsys):
    assert main(["bogus"]) == 2

    err = capsys.readouterr().err
    assert "unknown command" in err