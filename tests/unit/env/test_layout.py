from agent_harness.env import envfile, layout

MASTER = (
    "# Generated top block.\n#\n# Every other file follows it.\n\n"
    "# Database\n# The URL.\nDATABASE_URL=\nDATABASE_POOL=5\n\n"
    "# Queue\nQUEUE_URL=\n\n"
    "# Mail\nMAIL_KEY=\nMAIL_FROM=\n"
)


def shape() -> layout.Shape:
    return layout.shape_of_master(MASTER)


def test_reads_the_blocks_and_keys_of_a_master() -> None:
    assert shape().blocks == [
        ("# Database", ["DATABASE_URL", "DATABASE_POOL"]),
        ("# Queue", ["QUEUE_URL"]),
        ("# Mail", ["MAIL_KEY", "MAIL_FROM"]),
    ]


def test_puts_a_new_key_in_its_master_block_and_order() -> None:
    env = envfile.parse("# Database\nDATABASE_POOL=9\n\n# Mail\nMAIL_FROM=a\n")

    layout.put(env, "DATABASE_URL", ["DATABASE_URL=x"], shape())
    layout.put(env, "MAIL_KEY", ["MAIL_KEY=y"], shape())

    assert env.render() == (
        "# Database\nDATABASE_URL=x\nDATABASE_POOL=9\n\n# Mail\nMAIL_KEY=y\nMAIL_FROM=a\n"
    )


def test_inserts_a_missing_block_in_the_master_order() -> None:
    env = envfile.parse("# Database\nDATABASE_URL=x\n\n# Mail\nMAIL_KEY=y\n")

    state = layout.put(env, "QUEUE_URL", ["QUEUE_URL=z"], shape())

    assert state == "added"
    assert [b.header for b in env.blocks] == ["# Database", "# Queue", "# Mail"]


def test_puts_a_key_the_master_lacks_under_other() -> None:
    env = envfile.parse("# Mail\nMAIL_KEY=y\n")

    layout.put(env, "STRAY_KEY", ["STRAY_KEY=1"], shape())

    assert env.render() == "# Mail\nMAIL_KEY=y\n\n# Other\nSTRAY_KEY=1\n"


def test_replaces_an_existing_entry_in_place() -> None:
    env = envfile.parse("# Mail\nMAIL_FROM=a\nMAIL_KEY=old\n")

    assert layout.put(env, "MAIL_KEY", ["MAIL_KEY=new"], shape()) == "replaced"
    assert env.render() == "# Mail\nMAIL_FROM=a\nMAIL_KEY=new\n"


def test_reorders_a_file_to_its_master_and_names_the_extra_keys() -> None:
    env = envfile.parse("# Mail\nMAIL_FROM=a\nMAIL_KEY=b\n\n# Database\nEXTRA=c\nDATABASE_URL=d\n")

    fixed, extra = layout.reorder(env, shape())

    assert extra == ["EXTRA"]
    assert fixed.render() == (
        "# Database\nDATABASE_URL=d\n\n# Mail\nMAIL_KEY=b\nMAIL_FROM=a\n\n# Other\nEXTRA=c\n"
    )


def test_finds_a_key_out_of_the_master_order() -> None:
    texts = {".env.example": MASTER, ".env.local": "# Mail\nMAIL_FROM=a\nMAIL_KEY=b\n"}

    findings = layout.check_texts(texts, {name: name for name in texts})

    assert [str(f) for f in findings] == [
        ".env.local:3 L4 key MAIL_KEY is out of the master's key order"
    ]


def test_finds_no_problem_in_a_clean_folder() -> None:
    texts = {".env.example": MASTER, ".env.local": "# Database\nDATABASE_URL=x\n"}

    assert layout.check_texts(texts, {name: name for name in texts}) == []


def test_treats_a_push_marker_and_a_temp_file_as_no_env_file() -> None:
    assert layout.is_env_name(".env.production")
    assert not layout.is_env_name(".env.production.pushed-store")
    assert not layout.is_env_name(".env.local.bak")
    assert not layout.is_env_name(".env.local.tmp")
    assert not layout.is_env_name("env.production")


def test_builds_a_shape_from_declared_blocks() -> None:
    built = layout.shape_of_blocks([("A", "App"), ("B", "Mail"), ("C", "App")])

    assert built.blocks == [("# App", ["A", "C"]), ("# Mail", ["B"])]
