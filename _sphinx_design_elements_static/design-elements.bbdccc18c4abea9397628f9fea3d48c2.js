document.addEventListener("DOMContentLoaded", () => {
    console.info("Initializing sphinx-design-elements");
    setup_dropdown_group();
});

function setup_dropdown_group() {

    // Select all relevant detail elements nested within container elements using the `dropdown-group` class.
    const dropdown_details = document.querySelectorAll(".dropdown-group details");

    // Add event listener for special toggling.
    dropdown_details.forEach((details) => {
        details.addEventListener("toggle", toggleOpenGroup);
    });

    // When toggling elements, exclusively open one element, and close all others.
    function toggleOpenGroup(e) {
        if (this.open) {
            dropdown_details.forEach((details) => {
                if (details !== this && details.open) {
                    details.open = false;
                }
            });
        }
    }
}
