def main() -> None:
    from Brain import Brain
    from Console import already_running

    if already_running():
        print("Ordis is already running. Look for the icon by the clock.")
        return

    try:
        Brain().run()
    except KeyboardInterrupt:
        print("\nOrdis -> Bye :<")


def tray() -> None:
    import Tray

    Tray.main()


if __name__ == "__main__":
    main()
